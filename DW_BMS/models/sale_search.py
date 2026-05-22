from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    customer_phone = fields.Char(
        string="Customer Phone",
        compute="_compute_customer_phone",
        store=True,
    )

    @api.depends('partner_id.phone', 'partner_id.mobile')
    def _compute_customer_phone(self):

        for rec in self:

            rec.customer_phone = (
                rec.partner_id.mobile
                or rec.partner_id.phone
                or ''
            )

    def action_confirm(self):

        for rec in self:

            missing_fields = []

            if not (rec.partner_id.mobile or rec.partner_id.phone):
                missing_fields.append("Mobile Number")

            if not rec.partner_id.state_id:
                missing_fields.append("State")

            if not rec.partner_id.zip:
                missing_fields.append("Pincode")

            if missing_fields:
                raise ValidationError(
                    _(
                        "Cannot confirm quotation.\n\n"
                        "Customer '%s' is missing:\n- %s"
                    ) % (
                        rec.partner_id.display_name,
                        "\n- ".join(missing_fields)
                    )
                )

        return super().action_confirm()


