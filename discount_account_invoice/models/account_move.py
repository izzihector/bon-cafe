# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, float_compare

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _recompute_tax_lines(self, recompute_tax_base_amount=False, tax_rep_lines_to_recompute=None):
        """ Compute the dynamic tax lines of the journal entry.
        :param recompute_tax_base_amount: Flag forcing only the recomputation of the `tax_base_amount` field.
        """
        self.ensure_one()
        in_draft_mode = self != self._origin

        def _serialize_tax_grouping_key(grouping_dict):
            ''' Serialize the dictionary values to be used in the taxes_map.
            :param grouping_dict: The values returned by '_get_tax_grouping_key_from_tax_line' or '_get_tax_grouping_key_from_base_line'.
            :return: A string representing the values.
            '''
            return '-'.join(str(v) for v in grouping_dict.values())

        def _compute_base_line_taxes(base_line):
            ''' Compute taxes amounts both in company currency / foreign currency as the ratio between
            amount_currency & balance could not be the same as the expected currency rate.
            The 'amount_currency' value will be set on compute_all(...)['taxes'] in multi-currency.
            :param base_line:   The account.move.line owning the taxes.
            :return:            The result of the compute_all method.
            '''
            move = base_line.move_id

            if move.is_invoice(include_receipts=True):
                handle_price_include = True
                sign = -1 if move.is_inbound() else 1
                quantity = base_line.quantity
                is_refund = move.move_type in ('out_refund', 'in_refund')

                if base_line.discount_type and base_line.discount_type == 'fixed':
                    price_unit_wo_discount = sign * base_line.line_sub_total + base_line.discount_fixed
                    # price_unit_wo_discount = ((base_line.discount_fixed) / (base_line.price_unit * base_line.quantity)) * 100 or 0.00
                    print("4444444444444", price_unit_wo_discount)
                else:
                    price_unit_wo_discount = sign * base_line.price_unit * (1 - (base_line.discount / 100.0))



            else:
                handle_price_include = False
                quantity = 1.0
                tax_type = base_line.tax_ids[0].type_tax_use if base_line.tax_ids else None
                is_refund = (tax_type == 'sale' and base_line.debit) or (tax_type == 'purchase' and base_line.credit)
                price_unit_wo_discount = base_line.amount_currency

            return base_line.tax_ids._origin.with_context(force_sign=move._get_tax_force_sign()).compute_all(
                price_unit_wo_discount,
                currency=base_line.currency_id,
                quantity=quantity,
                product=base_line.product_id,
                partner=base_line.partner_id,
                is_refund=is_refund,
                handle_price_include=handle_price_include,
                include_caba_tags=move.always_tax_exigible,
            )

        taxes_map = {}

        # ==== Add tax lines ====
        to_remove = self.env['account.move.line']
        for line in self.line_ids.filtered('tax_repartition_line_id'):
            grouping_dict = self._get_tax_grouping_key_from_tax_line(line)
            grouping_key = _serialize_tax_grouping_key(grouping_dict)
            if grouping_key in taxes_map:
                # A line with the same key does already exist, we only need one
                # to modify it; we have to drop this one.
                to_remove += line
            else:
                taxes_map[grouping_key] = {
                    'tax_line': line,
                    'amount': 0.0,
                    'tax_base_amount': 0.0,
                    'grouping_dict': False,
                }
        if not recompute_tax_base_amount:
            self.line_ids -= to_remove

        # ==== Mount base lines ====
        for line in self.line_ids.filtered(lambda line: not line.tax_repartition_line_id):
            # Don't call compute_all if there is no tax.
            if not line.tax_ids:
                if not recompute_tax_base_amount:
                    line.tax_tag_ids = [(5, 0, 0)]
                continue

            compute_all_vals = _compute_base_line_taxes(line)

            # Assign tags on base line
            if not recompute_tax_base_amount:
                line.tax_tag_ids = compute_all_vals['base_tags'] or [(5, 0, 0)]

            for tax_vals in compute_all_vals['taxes']:
                grouping_dict = self._get_tax_grouping_key_from_base_line(line, tax_vals)
                grouping_key = _serialize_tax_grouping_key(grouping_dict)

                tax_repartition_line = self.env['account.tax.repartition.line'].browse(
                    tax_vals['tax_repartition_line_id'])
                tax = tax_repartition_line.invoice_tax_id or tax_repartition_line.refund_tax_id

                taxes_map_entry = taxes_map.setdefault(grouping_key, {
                    'tax_line': None,
                    'amount': 0.0,
                    'tax_base_amount': 0.0,
                    'grouping_dict': False,
                })
                taxes_map_entry['amount'] += tax_vals['amount']
                taxes_map_entry['tax_base_amount'] += self._get_base_amount_to_display(tax_vals['base'],
                                                                                       tax_repartition_line,
                                                                                       tax_vals['group'])
                taxes_map_entry['grouping_dict'] = grouping_dict

        # ==== Pre-process taxes_map ====
        taxes_map = self._preprocess_taxes_map(taxes_map)

        # ==== Process taxes_map ====
        for taxes_map_entry in taxes_map.values():
            # The tax line is no longer used in any base lines, drop it.
            if taxes_map_entry['tax_line'] and not taxes_map_entry['grouping_dict']:
                if not recompute_tax_base_amount:
                    self.line_ids -= taxes_map_entry['tax_line']
                continue

            currency = self.env['res.currency'].browse(taxes_map_entry['grouping_dict']['currency_id'])

            # Don't create tax lines with zero balance.
            if currency.is_zero(taxes_map_entry['amount']):
                if taxes_map_entry['tax_line'] and not recompute_tax_base_amount:
                    self.line_ids -= taxes_map_entry['tax_line']
                continue

            # tax_base_amount field is expressed using the company currency.
            tax_base_amount = currency._convert(taxes_map_entry['tax_base_amount'], self.company_currency_id,
                                                self.company_id, self.date or fields.Date.context_today(self))

            # Recompute only the tax_base_amount.
            if recompute_tax_base_amount:
                if taxes_map_entry['tax_line']:
                    taxes_map_entry['tax_line'].tax_base_amount = tax_base_amount
                continue

            balance = currency._convert(
                taxes_map_entry['amount'],
                self.company_currency_id,
                self.company_id,
                self.date or fields.Date.context_today(self),
            )
            to_write_on_line = {
                'amount_currency': taxes_map_entry['amount'],
                'currency_id': taxes_map_entry['grouping_dict']['currency_id'],
                'debit': balance > 0.0 and balance or 0.0,
                'credit': balance < 0.0 and -balance or 0.0,
                'tax_base_amount': tax_base_amount,
            }

            if taxes_map_entry['tax_line']:
                # Update an existing tax line.
                if tax_rep_lines_to_recompute and taxes_map_entry[
                    'tax_line'].tax_repartition_line_id not in tax_rep_lines_to_recompute:
                    continue

                taxes_map_entry['tax_line'].update(to_write_on_line)
            else:
                # Create a new tax line.
                create_method = in_draft_mode and self.env['account.move.line'].new or self.env[
                    'account.move.line'].create
                tax_repartition_line_id = taxes_map_entry['grouping_dict']['tax_repartition_line_id']
                tax_repartition_line = self.env['account.tax.repartition.line'].browse(tax_repartition_line_id)

                if tax_rep_lines_to_recompute and tax_repartition_line not in tax_rep_lines_to_recompute:
                    continue

                tax = tax_repartition_line.invoice_tax_id or tax_repartition_line.refund_tax_id
                taxes_map_entry['tax_line'] = create_method({
                    **to_write_on_line,
                    'name': tax.name,
                    'move_id': self.id,
                    'company_id': self.company_id.id,
                    'company_currency_id': self.company_currency_id.id,
                    'tax_base_amount': tax_base_amount,
                    'exclude_from_invoice_tab': True,
                    **taxes_map_entry['grouping_dict'],
                })

            if in_draft_mode:
                taxes_map_entry['tax_line'].update(
                    taxes_map_entry['tax_line']._get_fields_onchange_balance(force_computation=True))

    # def _recompute_tax_lines(self, recompute_tax_base_amount=False):
    #     ''' Compute the dynamic tax lines of the journal entry.
    #
    #     :param lines_map: The line_ids dispatched by type containing:
    #         * base_lines: The lines having a tax_ids set.
    #         * tax_lines: The lines having a tax_line_id set.
    #         * terms_lines: The lines generated by the payment terms of the invoice.
    #         * rounding_lines: The cash rounding lines of the invoice.
    #     '''
    #     self.ensure_one()
    #     in_draft_mode = self != self._origin
    #
    #     def _serialize_tax_grouping_key(grouping_dict):
    #         ''' Serialize the dictionary values to be used in the taxes_map.
    #         :param grouping_dict: The values returned by '_get_tax_grouping_key_from_tax_line' or '_get_tax_grouping_key_from_base_line'.
    #         :return: A string representing the values.
    #         '''
    #         return '-'.join(str(v) for v in grouping_dict.values())
    #
    #     def _compute_base_line_taxes(base_line):
    #         ''' Compute taxes amounts both in company currency / foreign currency as the ratio between
    #         amount_currency & balance could not be the same as the expected currency rate.
    #         The 'amount_currency' value will be set on compute_all(...)['taxes'] in multi-currency.
    #         :param base_line:   The account.move.line owning the taxes.
    #         :return:            The result of the compute_all method.
    #         '''
    #         move = base_line.move_id
    #
    #         if move.is_invoice(include_receipts=True):
    #             handle_price_include = True
    #             sign = -1 if move.is_inbound() else 1
    #             quantity = base_line.quantity
    #             is_refund = move.move_type in ('out_refund', 'in_refund')
    #             if base_line.discount_type and base_line.discount_type == 'fixed':
    #                 price_unit_wo_discount = sign * (base_line.price_unit - (base_line.discount / (base_line.quantity or 1.0)))
    #             else:
    #                 price_unit_wo_discount = sign * base_line.price_unit * (1 - (base_line.discount / 100.0))
    #         else:
    #             handle_price_include = False
    #             quantity = 1.0
    #             tax_type = base_line.tax_ids[0].type_tax_use if base_line.tax_ids else None
    #             is_refund = (tax_type == 'sale' and base_line.debit) or (tax_type == 'purchase' and base_line.credit)
    #             price_unit_wo_discount = base_line.balance
    #
    #         balance_taxes_res = base_line.tax_ids._origin.with_context(force_sign=move._get_tax_force_sign()).compute_all(
    #             price_unit_wo_discount,
    #             currency=base_line.currency_id,
    #             quantity=quantity,
    #             product=base_line.product_id,
    #             partner=base_line.partner_id,
    #             is_refund=is_refund,
    #             handle_price_include=handle_price_include,
    #         )
    #
    #         if move.move_type == 'entry':
    #             repartition_field = is_refund and 'refund_repartition_line_ids' or 'invoice_repartition_line_ids'
    #             repartition_tags = base_line.tax_ids.flatten_taxes_hierarchy().mapped(repartition_field).filtered(lambda x: x.repartition_type == 'base').tag_ids
    #             tags_need_inversion = (tax_type == 'sale' and not is_refund) or (tax_type == 'purchase' and is_refund)
    #             if tags_need_inversion:
    #                 balance_taxes_res['base_tags'] = base_line._revert_signed_tags(repartition_tags).ids
    #                 for tax_res in balance_taxes_res['taxes']:
    #                     tax_res['tag_ids'] = base_line._revert_signed_tags(self.env['account.account.tag'].browse(tax_res['tag_ids'])).ids
    #
    #         return balance_taxes_res
    #
    #     taxes_map = {}
    #
    #     # ==== Add tax lines ====
    #     to_remove = self.env['account.move.line']
    #     for line in self.line_ids.filtered('tax_repartition_line_id'):
    #         grouping_dict = self._get_tax_grouping_key_from_tax_line(line)
    #         grouping_key = _serialize_tax_grouping_key(grouping_dict)
    #         if grouping_key in taxes_map:
    #             # A line with the same key does already exist, we only need one
    #             # to modify it; we have to drop this one.
    #             to_remove += line
    #         else:
    #             taxes_map[grouping_key] = {
    #                 'tax_line': line,
    #                 'amount': 0.0,
    #                 'tax_base_amount': 0.0,
    #                 'grouping_dict': False,
    #             }
    #     if not recompute_tax_base_amount:
    #         self.line_ids -= to_remove
    #
    #     # ==== Mount base lines ====
    #     for line in self.line_ids.filtered(lambda line: not line.tax_repartition_line_id):
    #         # Don't call compute_all if there is no tax.
    #         if not line.tax_ids:
    #             if not recompute_tax_base_amount:
    #                 line.tax_tag_ids = [(5, 0, 0)]
    #             continue
    #
    #         compute_all_vals = _compute_base_line_taxes(line)
    #
    #         # Assign tags on base line
    #         if not recompute_tax_base_amount:
    #             line.tax_tag_ids = compute_all_vals['base_tags'] or [(5, 0, 0)]
    #
    #         always_tax_exigible = True
    #         for tax_vals in compute_all_vals['taxes']:
    #             grouping_dict = self._get_tax_grouping_key_from_base_line(line, tax_vals)
    #             grouping_key = _serialize_tax_grouping_key(grouping_dict)
    #
    #             tax_repartition_line = self.env['account.tax.repartition.line'].browse(tax_vals['tax_repartition_line_id'])
    #             tax = tax_repartition_line.invoice_tax_id or tax_repartition_line.refund_tax_id
    #
    #             if tax.tax_exigibility == 'on_payment':
    #                 always_tax_exigible = False
    #
    #             taxes_map_entry = taxes_map.setdefault(grouping_key, {
    #                 'tax_line': None,
    #                 'amount': 0.0,
    #                 'tax_base_amount': 0.0,
    #                 'grouping_dict': False,
    #             })
    #             taxes_map_entry['amount'] += tax_vals['amount']
    #             taxes_map_entry['tax_base_amount'] += self._get_base_amount_to_display(tax_vals['base'], tax_repartition_line, tax_vals['group'])
    #             taxes_map_entry['grouping_dict'] = grouping_dict
    #         if not recompute_tax_base_amount:
    #             line.move_id.always_tax_exigible = always_tax_exigible
    #
    #     # ==== Process taxes_map ====
    #     for taxes_map_entry in taxes_map.values():
    #         # The tax line is no longer used in any base lines, drop it.
    #         if taxes_map_entry['tax_line'] and not taxes_map_entry['grouping_dict']:
    #             if not recompute_tax_base_amount:
    #                 self.line_ids -= taxes_map_entry['tax_line']
    #             continue
    #
    #         currency = self.env['res.currency'].browse(taxes_map_entry['grouping_dict']['currency_id'])
    #
    #         # Don't create tax lines with zero balance.
    #         if currency.is_zero(taxes_map_entry['amount']):
    #             if taxes_map_entry['tax_line'] and not recompute_tax_base_amount:
    #                 self.line_ids -= taxes_map_entry['tax_line']
    #             continue
    #
    #         # tax_base_amount field is expressed using the company currency.
    #         tax_base_amount = currency._convert(taxes_map_entry['tax_base_amount'], self.company_currency_id, self.company_id, self.date or fields.Date.context_today(self))
    #
    #         # Recompute only the tax_base_amount.
    #         if recompute_tax_base_amount:
    #             if taxes_map_entry['tax_line']:
    #                 taxes_map_entry['tax_line'].tax_base_amount = tax_base_amount
    #             continue
    #
    #         balance = currency._convert(
    #             taxes_map_entry['amount'],
    #             self.journal_id.company_id.currency_id,
    #             self.journal_id.company_id,
    #             self.date or fields.Date.context_today(self),
    #         )
    #         to_write_on_line = {
    #             'amount_currency': taxes_map_entry['amount'],
    #             'currency_id': taxes_map_entry['grouping_dict']['currency_id'],
    #             'debit': balance > 0.0 and balance or 0.0,
    #             'credit': balance < 0.0 and -balance or 0.0,
    #             'tax_base_amount': tax_base_amount,
    #         }
    #
    #         if taxes_map_entry['tax_line']:
    #             # Update an existing tax line.
    #             taxes_map_entry['tax_line'].update(to_write_on_line)
    #         else:
    #             create_method = in_draft_mode and self.env['account.move.line'].new or self.env['account.move.line'].create
    #             tax_repartition_line_id = taxes_map_entry['grouping_dict']['tax_repartition_line_id']
    #             tax_repartition_line = self.env['account.tax.repartition.line'].browse(tax_repartition_line_id)
    #             tax = tax_repartition_line.invoice_tax_id or tax_repartition_line.refund_tax_id
    #             taxes_map_entry['tax_line'] = create_method({
    #                 **to_write_on_line,
    #                 'name': tax.name,
    #                 'move_id': self.id,
    #                 'partner_id': line.partner_id.id,
    #                 'company_id': line.company_id.id,
    #                 'company_currency_id': line.company_currency_id.id,
    #                 'tax_base_amount': tax_base_amount,
    #                 'exclude_from_invoice_tab': True,
    #                 # 'always_tax_exigible': tax.tax_exigibility == 'on_invoice',
    #                 **taxes_map_entry['grouping_dict'],
    #             })
    #
    #         if in_draft_mode:
    #             taxes_map_entry['tax_line'].update(taxes_map_entry['tax_line']._get_fields_onchange_balance(force_computation=True))
    #
    # @api.depends('line_ids.matched_debit_ids.debit_move_id.move_id.payment_id.is_matched',
    #              'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual',
    #              'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual_currency',
    #              'line_ids.matched_credit_ids.credit_move_id.move_id.payment_id.is_matched',
    #              'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual',
    #              'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual_currency',
    #              'line_ids.debit',
    #              'line_ids.credit',
    #              'line_ids.currency_id',
    #              'line_ids.amount_currency',
    #              'line_ids.amount_residual',
    #              'line_ids.amount_residual_currency',
    #              'line_ids.payment_id.state',
    #              'line_ids.full_reconcile_id',
    #              'global_discount_type',
    #              'global_order_discount',)
    # def _compute_amount(self):
    #     for move in self:
    #
    #         if move.payment_state == 'invoicing_legacy':
    #             # invoicing_legacy state is set via SQL when setting setting field
    #             # invoicing_switch_threshold (defined in account_accountant).
    #             # The only way of going out of this state is through this setting,
    #             # so we don't recompute it here.
    #             move.payment_state = move.payment_state
    #             continue
    #
    #         total_untaxed = 0.0
    #         total_untaxed_currency = 0.0
    #         total_tax = 0.0
    #         total_tax_currency = 0.0
    #         total_to_pay = 0.0
    #         total_residual = 0.0
    #         total_residual_currency = 0.0
    #         total = 0.0
    #         total_currency = 0.0
    #         total_global_discount = 0.0
    #         total_discount = 0.0
    #         global_discount = 0.0
    #         global_discount_currency = 0.0
    #         currencies = set()
    #
    #         for line in move.line_ids:
    #             if line.currency_id and line in move._get_lines_onchange_currency():
    #                 currencies.add(line.currency_id)
    #
    #             if move.is_invoice(include_receipts=True):
    #                 # === Invoices ===
    #
    #                 if not line.exclude_from_invoice_tab:
    #                     # Untaxed amount.
    #                     total_untaxed += line.balance
    #                     total_untaxed_currency += line.amount_currency
    #                     total += line.balance
    #                     total_currency += line.amount_currency
    #                     total_discount += line.discount if line.discount_type == 'fixed' else line.quantity * line.price_unit * line.discount / 100.0
    #                 elif line.tax_line_id:
    #                     # Tax amount.
    #                     total_tax += line.balance
    #                     total_tax_currency += line.amount_currency
    #                     total += line.balance
    #                     total_currency += line.amount_currency
    #                 elif line.is_global_line:
    #                     # Global Discount amount.
    #                     global_discount = line.balance
    #                     global_discount_currency = line.amount_currency
    #                     total += line.balance
    #                     total_currency += line.amount_currency
    #                 elif line.account_id.user_type_id.type in ('receivable', 'payable'):
    #                     # Residual amount.
    #                     total_to_pay += line.balance
    #                     total_residual += line.amount_residual
    #                     total_residual_currency += line.amount_residual_currency
    #             else:
    #                 # === Miscellaneous journal entry ===
    #                 if line.debit:
    #                     total += line.balance
    #                     total_currency += line.amount_currency
    #
    #         if move.move_type == 'entry' or move.is_outbound():
    #             sign = 1
    #         else:
    #             sign = -1
    #         total_global_discount = -1 * sign * (global_discount_currency if len(
    #             currencies) == 1 else global_discount)
    #         total_discount += total_global_discount
    #         move.total_global_discount = total_global_discount
    #         move.total_discount = total_discount
    #         move.amount_untaxed = sign * (total_untaxed_currency if len(currencies) == 1 else total_untaxed)
    #         move.amount_tax = sign * (total_tax_currency if len(currencies) == 1 else total_tax)
    #         move.amount_total = sign * (total_currency if len(currencies) == 1 else total)
    #         move.amount_residual = -sign * (total_residual_currency if len(currencies) == 1 else total_residual)
    #         move.amount_untaxed_signed = -total_untaxed
    #         move.amount_tax_signed = -total_tax
    #         move.amount_total_signed = abs(total) if move.move_type == 'entry' else -total
    #         move.amount_residual_signed = total_residual
    #
    #         currency = len(currencies) == 1 and currencies.pop() or move.company_id.currency_id
    #
    #         # Compute 'payment_state'.
    #         new_pmt_state = 'not_paid' if move.move_type != 'entry' else False
    #
    #         if move.is_invoice(include_receipts=True) and move.state == 'posted':
    #
    #             if currency.is_zero(move.amount_residual):
    #                 reconciled_payments = move._get_reconciled_payments()
    #                 if not reconciled_payments or all(payment.is_matched for payment in reconciled_payments):
    #                     new_pmt_state = 'paid'
    #                 else:
    #                     new_pmt_state = move._get_invoice_in_payment_state()
    #             elif currency.compare_amounts(total_to_pay, total_residual) != 0:
    #                 new_pmt_state = 'partial'
    #
    #         if new_pmt_state == 'paid' and move.move_type in ('in_invoice', 'out_invoice', 'entry'):
    #             reverse_type = move.move_type == 'in_invoice' and 'in_refund' or move.move_type == 'out_invoice' and 'out_refund' or 'entry'
    #             reverse_moves = self.env['account.move'].search([('reversed_entry_id', '=', move.id), ('state', '=', 'posted'), ('move_type', '=', reverse_type)])
    #
    #             # We only set 'reversed' state in cas of 1 to 1 full reconciliation with a reverse entry; otherwise, we use the regular 'paid' state
    #             reverse_moves_full_recs = reverse_moves.mapped('line_ids.full_reconcile_id')
    #             if reverse_moves_full_recs.mapped('reconciled_line_ids.move_id').filtered(lambda x: x not in (reverse_moves + reverse_moves_full_recs.mapped('exchange_move_id'))) == move:
    #                 new_pmt_state = 'reversed'
    #
    #         move.payment_state = new_pmt_state

    @api.depends(
        'line_ids.matched_debit_ids.debit_move_id.move_id.payment_id.is_matched',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.matched_credit_ids.credit_move_id.move_id.payment_id.is_matched',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.debit',
        'line_ids.credit',
        'line_ids.currency_id',
        'line_ids.amount_currency',
        'line_ids.amount_residual',
        'line_ids.amount_residual_currency',
        'line_ids.payment_id.state',
        'line_ids.full_reconcile_id', 'invoice_line_ids.discount', 'invoice_line_ids.discount_fixed',
        'global_discount_type', 'discount_1', 'discount_2', 'global_order_discount', 'global_order_discount_2')
    def _compute_amount(self):
        res = super(AccountMove, self)._compute_amount()

        return res

    @api.depends('invoice_line_ids.price_total', 'global_order_discount', 'global_order_discount_2',
                 'global_discount_type', 'discount_1', 'discount_2')
    def _amount_all(self):
        for order in self:
            amount_untaxed = amount_tax = total_discount = 0.0
            for line in order.invoice_line_ids:
                amount_untaxed += line.price_subtotal
                if line.discount_type == 'fixed':
                    total_discount += line.discount_fixed
                else:
                    total_discount += line.price_unit * line.quantity * (
                            (line.discount or 0.0) / 100.0)

                amount_tax += line.price_total

            IrConfigPrmtrSudo = self.env['ir.config_parameter'].sudo()
            # discTax = IrConfigPrmtrSudo.get_param('purchase.global_discount_tax_po')
            discTax = IrConfigPrmtrSudo.get_param('account.global_discount_tax')

            if discTax == 'taxed':
                total = amount_untaxed + amount_tax
            else:
                total = amount_untaxed

            if order.global_discount_type == 'fixed':
                discount_1 = order.global_order_discount
                discount_2 = order.global_order_discount_2
            else:
                total_before_disc = sum(line.line_sub_total for line in order.invoice_line_ids)

                discount_1 = total_before_disc * (order.global_order_discount or 0.0) / 100
                discount_2 = (total_before_disc - discount_1) * (order.global_order_discount_2 or 0.0) / 100

            total_global_discount = discount_1 + discount_2
            # total -= total_global_discount
            # total_discount += total_global_discount

            if discTax != 'taxed':
                total += amount_tax

            order.update({
                # 'amount_untaxed': order.currency_id.round(amount_untaxed),
                # 'amount_tax': order.currency_id.round(amount_tax),
                'amount_before_discount_1': amount_untaxed + discount_2 + discount_1,
                'amount_before_discount_2': amount_untaxed + discount_2,
                'total_discount': total_discount,
                'discount_1': discount_1,
                'discount_2': discount_2,
            })

    discount_1 = fields.Monetary(string='Discount 1', store=True, readonly=True,
                                 compute='_amount_all', tracking=True)
    amount_before_discount_1 = fields.Monetary(string='Amount Before Disc', store=True, readonly=True,
                                               compute='_amount_all', tracking=True)
    amount_before_discount_2 = fields.Monetary(string='Amount Before Discount 2', store=True, readonly=True,
                                               compute='_amount_all', tracking=True)
    discount_2 = fields.Monetary(string='Discount 2', store=True, readonly=True,
                                 compute='_amount_all', tracking=True)
    total_discount = fields.Monetary(string='Total Discount', store=True, readonly=True,
                                     compute='_amount_all', tracking=True)

    global_discount_type = fields.Selection(
        [('fixed', 'Fixed'), ('percent', 'Percent')],
        string="Discount Type",
        default="percent",
        help="If global discount type 'Fixed' has been applied then no partial invoice will be generated for this order.")
    global_order_discount = fields.Float(string='Global Discount', store=True, tracking=True)
    global_order_discount_2 = fields.Float(string='Rate2', store=True, tracking=True)

    @api.onchange('global_order_discount', 'global_order_discount_2', 'global_discount_type', 'discount_1',
                  'discount_2')
    def _onchange_discount(self):
        for rec in self:
            discount = rec.discount_1 + rec.discount_2
            for line in rec.invoice_line_ids:
                if rec.global_discount_type == 'percent':
                    line.discount_type = rec.global_discount_type
                    total_before_disc = sum(line.line_sub_total for line in rec.invoice_line_ids)
                    discount_percent = discount / (total_before_disc / 100)
                    line.discount = discount_percent
                elif rec.global_discount_type == 'fixed':
                    line.discount_type = rec.global_discount_type
                    line.discount_fixed = discount / len(rec.invoice_line_ids)
                    discount_percent = (discount / len(rec.invoice_line_ids)) / (line.line_sub_total / 100)
                    line.discount = discount_percent
                line._onchange_price_subtotal()
                line._onchange_mark_recompute_taxes()
            rec._onchange_invoice_line_ids()

    # @api.onchange('global_discount_type', 'total_discount','global_order_discount','global_order_discount')
    # def _onchange_global_order_discount(self):
    #     if not self.global_order_discount:
    #         global_discount_line = self.line_ids.filtered(lambda line: line.is_global_line)
    #         self.line_ids -= global_discount_line
    #
    #     if not self.global_discount_type:
    #         self.global_order_discount = 0.0
    #     #
    #     self._recompute_dynamic_lines()
    #     #
    #



    # def _recompute_global_discount_lines(self):
    #     ''' Compute the dynamic global discount lines of the journal entry.'''
    #     self.ensure_one()
    #     self = self.with_company(self.company_id)
    #     in_draft_mode = self != self._origin
    #     today = fields.Date.context_today(self)
    #
    #     def _compute_payment_terms(self):
    #         sign = 1 if self.is_inbound() else -1
    #
    #         IrConfigPrmtrSudo = self.env['ir.config_parameter'].sudo()
    #         discTax = IrConfigPrmtrSudo.get_param('account.global_discount_tax')
    #         if not discTax:
    #             discTax = 'untax'
    #
    #         discount_balance = 0.0
    #
    #         total = self.amount_untaxed + self.amount_tax
    #         if discTax != 'taxed':
    #             total = self.amount_untaxed
    #
    #         if self.global_discount_type == 'fixed':
    #             discount_balance = sign * (self.global_order_discount or 0.0)
    #         else:
    #             discount_balance = sign * (total * (self.global_order_discount or 0.0) / 100)
    #
    #         if self.currency_id == self.company_id.currency_id:
    #             discount_amount_currency = discount_balance
    #         else:
    #             discount_amount_currency = discount_balance
    #             discount_balance = self.currency_id._convert(
    #                 discount_amount_currency, self.company_id.currency_id, self.company_id, self.date)
    #
    #         if self.invoice_payment_term_id:
    #             date_maturity = self.invoice_date or today
    #         else:
    #             date_maturity = self.invoice_date_due or self.invoice_date or today
    #         return [(date_maturity, discount_balance, discount_amount_currency)]
    #
    #     def _compute_diff_global_discount_lines(self, existing_global_lines, account, to_compute):
    #         new_global_discount_lines = self.env['account.move.line']
    #         for date_maturity, balance, amount_currency in to_compute:
    #             if existing_global_lines:
    #                 candidate = existing_global_lines[0]
    #                 candidate.update({
    #                     'date_maturity': date_maturity,
    #                     'amount_currency': amount_currency,
    #                     'debit': balance > 0.0 and balance or 0.0,
    #                     'credit': balance < 0.0 and -balance or 0.0,
    #                 })
    #             else:
    #                 create_method = in_draft_mode and self.env['account.move.line'].new or self.env['account.move.line'].create
    #                 candidate = create_method({
    #                     'name': 'Global Discount',
    #                     'debit': balance > 0.0 and balance or 0.0,
    #                     'credit': balance < 0.0 and -balance or 0.0,
    #                     'quantity': 1.0,
    #                     'amount_currency': amount_currency,
    #                     'date_maturity': date_maturity,
    #                     'move_id': self.id,
    #                     'currency_id': self.currency_id.id if self.currency_id != self.company_id.currency_id else False,
    #                     'account_id': account.id,
    #                     'partner_id': self.commercial_partner_id.id,
    #                     'exclude_from_invoice_tab': True,
    #                     'is_global_line': True,
    #                 })
    #             new_global_discount_lines += candidate
    #             if in_draft_mode:
    #                 candidate.update(candidate._get_fields_onchange_balance())
    #         return new_global_discount_lines
    #
    #     existing_global_lines = self.line_ids.filtered(lambda line: line.is_global_line)
    #     others_lines = self.line_ids.filtered(lambda line: not line.is_global_line)
    #
    #     if not others_lines:
    #         self.line_ids -= existing_global_lines
    #         return
    #
    #     if existing_global_lines:
    #         account = existing_global_lines[0].account_id
    #     else:
    #         IrConfigPrmtr = self.env['ir.config_parameter'].sudo()
    #         if self.move_type in ['out_invoice', 'out_refund', 'out_receipt']:
    #             account = self.env.company.discount_account_invoice
    #         else:
    #             account = self.env.company.discount_account_bill
    #         if not account:
    #             raise UserError(
    #                 _("Global Discount!\nPlease first set account for global discount in account setting."))
    #
    #     to_compute = _compute_payment_terms(self)
    #
    #     new_terms_lines = _compute_diff_global_discount_lines(self, existing_global_lines, account, to_compute)
    #
    #     self.line_ids -= existing_global_lines - new_terms_lines
    #
    # def _recompute_dynamic_lines(self, recompute_all_taxes=False, recompute_tax_base_amount=False):
    #     ''' Recompute all lines that depend of others.
    #
    #     For example, tax lines depends of base lines (lines having tax_ids set). This is also the case of cash rounding
    #     lines that depend of base lines or tax lines depending the cash rounding strategy. When a payment term is set,
    #     this method will auto-balance the move with payment term lines.
    #
    #     :param recompute_all_taxes: Force the computation of taxes. If set to False, the computation will be done
    #                                 or not depending of the field 'recompute_tax_line' in lines.
    #     '''
    #     for invoice in self:
    #         if invoice.global_order_discount:
    #             # Dispatch lines and pre-compute some aggregated values like taxes.
    #             for line in invoice.line_ids:
    #                 if line.recompute_tax_line:
    #                     recompute_all_taxes = True
    #                     line.recompute_tax_line = False
    #
    #             # Compute taxes.
    #             if recompute_all_taxes:
    #                 invoice._recompute_tax_lines()
    #             if recompute_tax_base_amount:
    #                 invoice._recompute_tax_lines(recompute_tax_base_amount=True)
    #
    #             if invoice.is_invoice(include_receipts=True):
    #
    #                 # Compute cash rounding.
    #                 invoice._recompute_cash_rounding_lines()
    #
    #                 # Compute global discount line.
    #                 invoice._recompute_global_discount_lines()
    #
    #                 # Compute payment terms.
    #                 invoice._recompute_payment_terms_lines()
    #
    #                 # Only synchronize one2many in onchange.
    #                 if invoice != invoice._origin:
    #                     invoice.invoice_line_ids = invoice.line_ids.filtered(
    #                         lambda line: not line.exclude_from_invoice_tab)
    #         else:
    #             super(AccountMove, invoice)._recompute_dynamic_lines(recompute_all_taxes=False)




class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    is_global_line = fields.Boolean(string='Global Discount Line',
                                    help="This field is used to separate global discount line.")

    @api.depends('price_unit', 'discount_type', 'discount', 'discount_fixed', 'tax_ids', 'quantity')
    def _get_line_subtotal(self):
        for line in self:
            price = line.price_unit
            quantity = line.quantity
            taxes = line.tax_ids.compute_all(**line._prepare_compute_all_values())
            line.line_sub_total = taxes['total_excluded']

    line_sub_total = fields.Monetary(compute='_get_line_subtotal',
                                     string='Line Subtotal', readonly=True, store=True)
    discount_type = fields.Selection(
        [('fixed', 'Fixed'), ('percent', 'Percent')],
        string="Discount Type",
        default="percent")

    discount_fixed = fields.Float(
        string="Disc.",
        digits="Product Price",
        default=0.00,
        help="Fixed amount discount.",
    )

    def _prepare_compute_all_values(self):
        # vals = super(PurchaseOrderLine, self)._prepare_compute_all_values()
        vals = {}
        price = self.price_unit * (1 - (self.discount or 0.0) / 100.0)
        quantity = self.quantity
        # if self.discount_type == 'fixed':
        #     quantity=1.0
        #     price = self.price_unit * self.quantity - (self.discount or 0.0)
        # else:
        quantity = 1.0
        price = self.price_unit * self.quantity
        vals.update({
            'price_unit': price,
            'quantity': quantity,
        })
        return vals

    @api.onchange("discount")
    def _onchange_discount(self):
        if self.discount:
            self.discount_fixed = 0.0

    @api.onchange("discount_fixed")
    def _onchange_discount_fixed(self):
        if self.discount_fixed:
            self.discount = 0.0




    @api.onchange("quantity", "discount", "price_unit", "tax_ids", "discount_type", "discount_fixed")
    def _onchange_price_subtotal(self):
        return super(AccountMoveLine, self)._onchange_price_subtotal()

    @api.model
    def _get_price_total_and_subtotal_model(
            self,
            price_unit,
            quantity,
            discount,
            currency,
            product,
            partner,
            taxes,
            move_type,
    ):
        if self.discount_type == 'fixed':
            discount = ((self.discount_fixed) / (price_unit * quantity)) * 100 or 0.00
        return super(AccountMoveLine, self)._get_price_total_and_subtotal_model(
            price_unit, quantity, discount, currency, product, partner, taxes, move_type
        )

    @api.model
    def _get_fields_onchange_balance_model(
            self,
            quantity,
            discount,
            amount_currency,
            move_type,
            currency,
            taxes,
            price_subtotal,
            force_computation=False,
    ):
        if self.discount_type == 'fixed':
            discount = ((self.discount_fixed) / (self.price_unit * quantity)) * 100 or 0.00

        return super(AccountMoveLine, self)._get_fields_onchange_balance_model(
            quantity,
            discount,
            amount_currency,
            move_type,
            currency,
            taxes,
            price_subtotal,
            force_computation=force_computation,
        )

    @api.model_create_multi
    def create(self, vals_list):
        prev_discount = []
        for vals in vals_list:
            if vals.get("discount_fixed"):
                prev_discount.append(
                    {"discount_fixed": vals.get("discount_fixed"), "discount": 0.00}
                )
                fixed_discount = (
                                         vals.get("discount_fixed") / vals.get("price_unit")
                                 ) * 100
                vals.update({"discount": fixed_discount, "discount_fixed": 0.00})
            elif vals.get("discount"):
                prev_discount.append({"discount": vals.get("discount")})
        res = super(AccountMoveLine, self).create(vals_list)
        i = 0
        for rec in res:
            if rec.discount and prev_discount:
                rec.write(prev_discount[i])
                i += 1
        return res
    # @api.model
    # def _get_price_total_and_subtotal_model(self, price_unit, quantity, discount, currency, product, partner, taxes, move_type):
    #     ''' This method is used to compute 'price_total' & 'price_subtotal'.
    #
    #     :param price_unit:  The current price unit.
    #     :param quantity:    The current quantity.
    #     :param discount:    The current discount.
    #     :param currency:    The line's currency.
    #     :param product:     The line's product.
    #     :param partner:     The line's partner.
    #     :param taxes:       The applied taxes.
    #     :param move_type:   The type of the move.
    #     :return:            A dictionary containing 'price_subtotal' & 'price_total'.
    #     '''
    #     res = {}
    #
    #     # Compute 'price_subtotal'.
    #     discount_type = ''
    #     if self._context and self._context.get('wk_vals_list', []):
    #         for vals in self._context.get('wk_vals_list', []):
    #             if price_unit == vals.get('price_unit', 0.0) and quantity == vals.get('quantity', 0.0) and discount == vals.get('discount', 0.0) and product.id == vals.get('product_id', False) and partner.id == vals.get('partner_id', False):
    #                 discount_type = vals.get('discount_type', '')
    #                 break
    #     discount_type = self.discount_type or discount_type or ''
    #
    #     if discount_type == 'fixed':
    #         line_discount_price_unit = price_unit * quantity - discount
    #         quantity = 1.0
    #     else:
    #         line_discount_price_unit = price_unit * (1 - (discount / 100.0))
    #
    #     subtotal = (quantity * line_discount_price_unit)
    #     # Compute 'price_total'.
    #     if taxes:
    #         force_sign = -1 if move_type in ('out_invoice', 'in_refund', 'out_receipt') else 1
    #         taxes_res = taxes._origin.with_context(force_sign=force_sign).compute_all(line_discount_price_unit,
    #             quantity=quantity, currency=currency, product=product, partner=partner, is_refund=move_type in ('out_refund', 'in_refund'))
    #
    #         res['price_subtotal'] = taxes_res['total_excluded']
    #         res['price_total'] = taxes_res['total_included']
    #     else:
    #         res['price_total'] = res['price_subtotal'] = subtotal
    #     #In case of multi currency, round before it's use for computing debit credit
    #     if currency:
    #         res = {k: currency.round(v) for k, v in res.items()}
    #     return res
    #
    # @api.model
    # def _get_fields_onchange_balance_model(self, quantity, discount, amount_currency, move_type, currency, taxes, price_subtotal, force_computation=False):
    #     ''' This method is used to recompute the values of 'quantity', 'discount', 'price_unit' due to a change made
    #     in some accounting fields such as 'balance'.
    #
    #     This method is a bit complex as we need to handle some special cases.
    #     For example, setting a positive balance with a 100% discount.
    #
    #     :param quantity:        The current quantity.
    #     :param discount:        The current discount.
    #     :param amount_currency: The new balance in line's currency.
    #     :param move_type:       The type of the move.
    #     :param currency:        The currency.
    #     :param taxes:           The applied taxes.
    #     :param price_subtotal:  The price_subtotal.
    #     :return:                A dictionary containing 'quantity', 'discount', 'price_unit'.
    #     '''
    #     if move_type in self.move_id.get_outbound_types():
    #         sign = 1
    #     elif move_type in self.move_id.get_inbound_types():
    #         sign = -1
    #     else:
    #         sign = 1
    #     amount_currency *= sign
    #
    #     # Avoid rounding issue when dealing with price included taxes. For example, when the price_unit is 2300.0 and
    #     # a 5.5% price included tax is applied on it, a balance of 2300.0 / 1.055 = 2180.094 ~ 2180.09 is computed.
    #     # However, when triggering the inverse, 2180.09 + (2180.09 * 0.055) = 2180.09 + 119.90 = 2299.99 is computed.
    #     # To avoid that, set the price_subtotal at the balance if the difference between them looks like a rounding
    #     # issue.
    #     if not force_computation and currency.is_zero(amount_currency - price_subtotal):
    #         return {}
    #
    #     taxes = taxes.flatten_taxes_hierarchy()
    #     if taxes and any(tax.price_include for tax in taxes):
    #         # Inverse taxes. E.g:
    #         #
    #         # Price Unit    | Taxes         | Originator Tax    |Price Subtotal     | Price Total
    #         # -----------------------------------------------------------------------------------
    #         # 110           | 10% incl, 5%  |                   | 100               | 115
    #         # 10            |               | 10% incl          | 10                | 10
    #         # 5             |               | 5%                | 5                 | 5
    #         #
    #         # When setting the balance to -200, the expected result is:
    #         #
    #         # Price Unit    | Taxes         | Originator Tax    |Price Subtotal     | Price Total
    #         # -----------------------------------------------------------------------------------
    #         # 220           | 10% incl, 5%  |                   | 200               | 230
    #         # 20            |               | 10% incl          | 20                | 20
    #         # 10            |               | 5%                | 10                | 10
    #         force_sign = -1 if move_type in ('out_invoice', 'in_refund', 'out_receipt') else 1
    #         taxes_res = taxes._origin.with_context(force_sign=force_sign).compute_all(amount_currency, currency=currency, handle_price_include=False)
    #         for tax_res in taxes_res['taxes']:
    #             tax = self.env['account.tax'].browse(tax_res['id'])
    #             if tax.price_include:
    #                 amount_currency += tax_res['amount']
    #
    #     discount_type = ''
    #     if self._context and self._context.get('wk_vals_list', []):
    #         for vals in self._context.get('wk_vals_list', []):
    #             if quantity == vals.get('quantity', 0.0) and discount == vals.get('discount', 0.0) and amount_currency == vals.get('amount_currency', 0.0) and price_subtotal == vals.get('price_subtotal', 0.0):
    #                 discount_type = vals.get('discount_type', '')
    #                 break
    #     discount_type = self.discount_type or discount_type or ''
    #     if discount_type == 'fixed':
    #         if amount_currency:
    #             vals = {
    #                 'quantity': quantity or 1.0,
    #                 'price_unit': (amount_currency + discount) / (quantity or 1.0),
    #             }
    #         else:
    #             vals = {'price_unit': 0.0}
    #     else:
    #        discount_factor = 1 - (discount / 100.0)
    #     if amount_currency and discount_factor:
    #         # discount != 100%
    #         vals = {
    #             'quantity': quantity or 1.0,
    #             'price_unit': amount_currency / discount_factor / (quantity or 1.0),
    #         }
    #     elif amount_currency and not discount_factor:
    #         # discount == 100%
    #         vals = {
    #             'quantity': quantity or 1.0,
    #             'discount': 0.0,
    #             'price_unit': amount_currency / (quantity or 1.0),
    #         }
    #     elif not discount_factor:
    #         # balance of line is 0, but discount  == 100% so we display the normal unit_price
    #         vals = {}
    #     else:
    #         # balance is 0, so unit price is 0 as well
    #         vals = {'price_unit': 0.0}
    #     return vals
    #
    #
    #
    #
    #
    #
    #
    #
    @api.onchange('amount_currency', 'currency_id', 'debit', 'credit', 'tax_ids', 'discount_fixed', 'account_id',
                  'price_unit',
                  'quantity')
    def _onchange_mark_recompute_taxes(self):
        return super(AccountMoveLine, self)._onchange_mark_recompute_taxes()
