from odoo import models, fields


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):

        for rec in self:

            # Detect IGST
            igst_applied = any(
                'IGST' in tax.name.upper()
                for line in rec.order_line
                for tax in line.tax_id
            )

            print("IGST =", igst_applied)
            print("TOTAL =", rec.amount_total)

            # IGST -> 49K
            if (
                igst_applied
                and rec.amount_total >= 49000
                and not self.env.context.get('warning_done')
            ):

                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Warning',
                    'res_model': 'sale.confirm.warning',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_sale_order_id': rec.id,
                        'default_warning_message': 'IGST amount crossed ₹49,000 limit.'
                    }
                }

            # NON IGST -> 99K
            elif (
                not igst_applied
                and rec.amount_total >= 99000
                and not self.env.context.get('warning_done')
            ):

                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Warning',
                    'res_model': 'sale.confirm.warning',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_sale_order_id': rec.id,
                        'default_warning_message': 'Amount is greater than ₹99,000.'
                    }
                }

        return super().action_confirm()


class SaleConfirmWarning(models.TransientModel):
    _name = 'sale.confirm.warning'
    _description = 'Sale Confirmation Warning'

    sale_order_id = fields.Many2one('sale.order')

    warning_message = fields.Text()

    def action_yes(self):

        return self.sale_order_id.with_context(
            warning_done=True
        ).action_confirm()