import re

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.osv.expression import OR, AND


class ResPartner(models.Model):
    _inherit = "res.partner"

    supplier_type = fields.Selection(
        [
            ('individual', 'Individual'),
            ('business', 'Business'),
        ],
        string="Supplier Type",
        default='individual'
    )

    customer_uid = fields.Char(
        string="UID",
        compute="_compute_customer_uid",
        # store=True
    )
    # phone = fields.Char(required=True)
    mobile = fields.Char(required=True)

    global_location_number = fields.Char(
        string="Global Location Number"
    )
    # NEW FIELD ADDED (SAFE)
    customer_type = fields.Selection(
        [
            ('wholesaler', 'Wholesaler'),
            ('retailer', 'Retailer'),
            ('end_user', 'End User'),
        ],
        string="Customer Type"
    )
    customer_type_master_id = fields.Many2one(
        "dw.customer.type",
        string="Customer Type (Master)",
        ondelete="set null",
    )

    @api.depends('name', 'mobile', 'phone', 'state_id', 'vat', 'zip')
    def _compute_customer_uid(self):

        for rec in self:

            # name = rec.name or ''
            name = (rec.name or '').strip()

            contact = (rec.mobile if rec.mobile else rec.phone or '').strip()

            state = rec.state_id.name or ''

            vat = rec.vat or ''

            zip_code = rec.zip or ''

            rec.customer_uid = f"{name}{contact}{state}{vat}{zip_code}"

    @api.constrains('phone', 'mobile')
    def _check_unique_contact_numbers(self):

        for partner in self:

            phone = (partner.phone or '').strip()
            mobile = (partner.mobile or '').strip()

            if not phone and not mobile:
                continue

            domain = [('id', '!=', partner.id)]

            search_domain = []

            if phone:
                search_domain += [
                    '|',
                    ('phone', '=', phone),
                    ('mobile', '=', phone),
                ]

            if mobile:

                if search_domain:
                    search_domain = ['|'] + search_domain

                search_domain += [
                    '|',
                    ('phone', '=', mobile),
                    ('mobile', '=', mobile),
                ]

            duplicate = self.search(
                domain + search_domain,
                limit=1
            )

            if duplicate:
                raise ValidationError(
                    _(
                        "This Phone/Mobile number already exists for contact: %s"
                    ) % duplicate.display_name
                )

    @api.constrains('name')
    def _check_partner_name(self):

        for rec in self:

            if rec.name:

                name = rec.name.strip()

                # No double spaces
                if '  ' in name:
                    raise ValidationError(
                        "Double spaces are not allowed in Name."
                    )

                # Only letters numbers and spaces
                if not re.match(r'^[A-Za-z0-9 ]+$', name):
                    raise ValidationError(
                        "Special characters are not allowed in Name."
                    )
    # ------------------------------------------------
    # Mobile validation only within the same customer
    # ------------------------------------------------
    @api.constrains('phone', 'mobile')
    def _check_unique_phone_mobile(self):
        for partner in self:
            if partner.phone and partner.mobile and partner.phone == partner.mobile:
                raise ValidationError(
                    "Mobile 1 and Mobile 2 cannot be the same number."
                )

    @api.constrains('name')
    def _check_duplicate_partner_name(self):
        for partner in self:
            partner_name = (partner.name or "").strip()
            if not partner_name:
                continue

            duplicate_exists = self.with_context(active_test=False).search_count([
                ("id", "!=", partner.id),
                ("name", "=ilike", partner_name),
            ])
            if duplicate_exists:
                raise ValidationError("Contact with this name already exists.")

    # -----------------------------------
    # GST REQUIRED FOR BUSINESS SUPPLIER
    # -----------------------------------
    @api.constrains('supplier_type', 'vat', 'supplier_rank')
    def _check_gst_for_business_supplier(self):
        for partner in self:
            if partner.supplier_rank > 0 and partner.supplier_type == 'business':
                if not partner.vat:
                    raise ValidationError(
                        "GST Number (Tax ID) is mandatory for Business Suppliers."
                    )

    # ------------------------------------------------
    # SEARCH BY NAME AND PHONE NUMBER IN SALES
    # ------------------------------------------------

    @api.model
    def _name_search(
        self,
        name='',
        args=None,
        operator='ilike',
        limit=100,
        name_get_uid=None,
        order=None,
    ):

        args = args or []

        domain = []

        if name:
            domain = OR([
                [('name', operator, name)],
                [('phone', operator, name)],
                [('mobile', operator, name)],
            ])

        return self._search(
            AND([domain, args]),
            limit=limit,
            access_rights_uid=name_get_uid,
        )

    def name_get(self):

        result = []

        for rec in self:

            display_name = rec.name or ''

            if rec.phone:
                display_name += ' - %s' % rec.phone

            elif rec.mobile:
                display_name += ' - %s' % rec.mobile

            result.append((rec.id, display_name))

        return result


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    hsn_code = fields.Char(
        string="HSN",
        related="product_id.l10n_in_hsn_code",
        store=True
    )



class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    hsn_code = fields.Char(
        string="HSN",
        related="product_id.l10n_in_hsn_code",
        store=True
    )

class AccountMove(models.Model):
    _inherit = "account.move"

    def copy(self, default=None):
        raise UserError(_("Duplicate is not allowed for Invoices."))
    
class AccountPayment(models.Model):
    _inherit = "account.payment"

    def copy(self, default=None):
        raise UserError(_("Duplicate is not allowed for Payments."))