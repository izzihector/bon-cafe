# -*- coding: utf-8 -*-
# Copyright 2019 Artem Shurshilov
# Odoo Proprietary License v1.0

# This software and associated files (the "Software") may only be used (executed,
# modified, executed after modifications) if you have purchased a valid license
# from the authors, typically via Odoo Apps, or if you have received a written
# agreement from the authors of the Software (see the COPYRIGHT file).

# You may develop Odoo modules that use the Software as a library (typically
# by depending on it, importing it and using its resources), but without copying
# any source code or material from the Software. You may distribute those
# modules under the license of your choice, provided that this license is
# compatible with the terms of the Odoo Proprietary License (For example:
# LGPL, MIT, or proprietary licenses similar to this one).

# It is forbidden to publish, distribute, sublicense, or sell copies of the Software
# or modified copies of the Software.

# The above copyright notice and this permission notice must be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
# DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
# ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from odoo import models, fields, api, _


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    geospatial_check_in_id = fields.Many2one('hr.attendance.geospatial', string="Location geospatial found in", readonly=True)
    geospatial_check_out_id = fields.Many2one('hr.attendance.geospatial', string="Location geospatial found out", readonly=True)
    geospatial_access_check_in = fields.Html(string="Geospatial in", readonly=True)
    geospatial_access_check_out = fields.Html(string="Geospatial out", readonly=True)


class HrAttendanceGEO(models.Model):
    _name = "hr.attendance.geospatial"

    name = fields.Char(string="Name of location")
    description = fields.Char(string="Description of location, display on checkin interface")
    company_id = fields.Many2one('res.company', "Company")
    employee_ids = fields.Many2many('hr.employee', string="Only for employees")
    the_geom2 = fields.GeoPolygon('NPA Shape')
