# -*- coding: utf-8 -*-
{
    'name': 'Gs HR Egypt Payroll',
    'version': '0.1',
    'category': 'Human Resources/Employees',
    "author": "Global Solutions",
    "website": "https://globalsolutions.dev",
    'depends': ['base', 'hr_payroll', ],
    'currency': 'EUR',

    'description': """
Egypt Payroll Rules.
  What`s new:-
   - Employee Details:-
      * Social Insurance Information (start date, number , end date).
      * Medical Insurance Information (date, number ).
      * Religion Selection. 
      * Military Status.
      * Calculated employee age form birth date.
      * Salary Items Based on Egyptian Common Payroll(Insurance basic salary ,Insurance variable salary,
        Allowances , Previous Annual Raises and custom allowances that you can add by your self 
        and get it`s amount in salary rules).
      * Employee Education  details.
      * calculated experience on the current company based on job start date.
   - Salary Rules:-
      * Salary rule for insurance basic salary. 
      * Salary rule for insurance variable salary.
      * Salary rule for Previous annual raises.
      * Salary rule for  employee allowances.
      * Salary rule for Employee insurance deduction share (14% of insurance basic salary + 
        11% of insurance variable salary).
      * Salary rule for Income tax deduction based on the last modification of Egyptian income taxes low in 21 jun 2017.
      
   - Salary Structure:-
      * New salary structure based on previous salary rules.
      
   - Reports:-
      * New PaySlips report template with employee details.
      
    -Translation:-
      * Arabic translation for all terms added in this module.
      * Fixing the name of the months that appear in payslip title while the system language is arabic.
      
    """,
    'data': [
        'security/ir.model.access.csv',
        'views/hr_employee_view.xml',
        'views/hr_contract_view.xml',
        'data/data.xml',
        'data/ir_cron.xml',
    ],
    'demo': [
        'demo/demo.xml',

    ],

    'license': 'OPL-1',
}
