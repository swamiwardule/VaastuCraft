from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
import io
import base64
import xlsxwriter

class JobWorkIssue(models.Model):
    _name = "dw.job.work.issue"
    _description = "Job Work Issue"
    _order = "issue_date desc, id desc"

    name = fields.Char(
        string="Slip Number",
        default="New",
        copy=False,
        readonly=True,
    )
    issue_date = fields.Datetime(
        string="Issue Date",
        default=fields.Datetime.now,
        required=True,
    )
    contractor_id = fields.Many2one(
        "res.partner",
        string="Contractor",
        required=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="draft",
        required=True,
        readonly=True,
        copy=False,
    )
    line_ids = fields.One2many(
        "dw.job.work.issue.line",
        "issue_id",
        string="Materials",
        copy=True,
    )
    remaining_qty = fields.Float(
        string="Remaining Quantity",
        compute="_compute_remaining_qty",
        store=True,
    )
    remaining_summary = fields.Char(
        string="Remaining Quantity",
        compute="_compute_remaining_summary",
    )
    receipt_id = fields.Many2one(
        "dw.job.work.receipt",
        string="Receipt",
        compute="_compute_receipt_id",
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    amount_untaxed = fields.Monetary(
        string="Untaxed Amount",
        compute="_compute_totals",
        store=True,
    )

    amount_tax = fields.Monetary(
        string="Tax",
        compute="_compute_totals",
        store=True,
    )

    amount_total = fields.Monetary(
        string="Total",
        compute="_compute_totals",
        store=True,
    )

    amount_due = fields.Monetary(
        string="Amount Due",
        compute="_compute_totals",
        store=True,
    )

    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
        store=True,
    )
    tax_label = fields.Char(
        string="Tax Label",
        compute="_compute_tax_label",
    )

    tax_amount_display = fields.Char(
        string="Tax Display",
        compute="_compute_tax_label",
    )

    cgst_amount = fields.Monetary(
        string="CGST",
        compute="_compute_tax_breakup",
        store=True,
    )

    sgst_amount = fields.Monetary(
        string="SGST",
        compute="_compute_tax_breakup",
        store=True,
    )

    igst_amount = fields.Monetary(
        string="IGST",
        compute="_compute_tax_breakup",
        store=True,
    )

    @api.onchange('contractor_id')
    def _onchange_contractor_id_update_taxes(self):

        for rec in self:
            for line in rec.line_ids:
                line._onchange_product_id_set_taxes()

    def _compute_receipt_id(self):
        receipt_map = {
            receipt.issue_id.id: receipt.id
            for receipt in self.env["dw.job.work.receipt"].search([("issue_id", "in", self.ids)])
        }
        for rec in self:
            rec.receipt_id = receipt_map.get(rec.id, False)

    @api.depends("line_ids.remaining_qty")
    def _compute_remaining_qty(self):
        for rec in self:
            rec.remaining_qty = sum(rec.line_ids.mapped("remaining_qty"))

    @api.depends("line_ids.remaining_qty", "line_ids.product_id", "line_ids.product_uom_id")
    def _compute_remaining_summary(self):
        for rec in self:
            parts = []
            for line in rec.line_ids.filtered(lambda l: l.product_id and l.remaining_qty):
                uom_name = line.product_uom_id.name or ""
                qty_text = f"{line.remaining_qty:g}"
                parts.append(
                    f"{line.product_id.display_name}: {qty_text}{(' ' + uom_name) if uom_name else ''}"
                )
            rec.remaining_summary = ", ".join(parts)

    def _check_duplicate_issue_products(self):
        for rec in self:
            seen_product_ids = set()
            duplicate_names = []
            for line in rec.line_ids.filtered(lambda l: l.product_id):
                if line.product_id.id in seen_product_ids:
                    duplicate_names.append(line.product_id.display_name)
                    continue
                seen_product_ids.add(line.product_id.id)
            if duplicate_names:
                raise ValidationError(
                    _(
                        "Duplicate raw material products are not allowed in Issue.\n"
                        "Duplicate product(s): %(products)s",
                        products=", ".join(sorted(set(duplicate_names))),
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("dw.job.work") or "New"
        records = super().create(vals_list)
        records._check_duplicate_issue_products()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._check_duplicate_issue_products()
        return res

    def _prepare_auto_receipt_vals(self):
        self.ensure_one()
        return {
            "issue_id": self.id,
            "contractor_id": self.contractor_id.id,
            "raw_line_ids": [
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                    },
                )
                for line in self.line_ids
                if line.product_id
            ],
        }

    def _create_auto_receipt_if_missing(self):
        receipt_model = self.env["dw.job.work.receipt"]
        for rec in self:
            existing_receipt = receipt_model.search([("issue_id", "=", rec.id)], limit=1)
            if existing_receipt or not rec.line_ids:
                continue
            receipt_model.create(rec._prepare_auto_receipt_vals())

    def action_print_issue_report(self):
        self.ensure_one()
        return self.env.ref("DW_JOB_WORK.action_report_job_work_issue").report_action(self)

    def action_confirm(self):
        stock_location = self.env.ref("stock.stock_location_stock")
        job_work_location = self.env.ref("DW_JOB_WORK.job_work_location")

        for rec in self:
            if rec.state != "draft":
                continue
            rec._check_duplicate_issue_products()
            if not rec.line_ids:
                raise ValidationError(_("Add at least one raw material line before confirming."))

            move_vals_list = []
            for line in rec.line_ids:
                if line.qty <= 0:
                    raise ValidationError(
                        _("Issued quantity must be greater than zero for all raw materials.")
                    )

                if line.product_free_qty <= 0:
                    raise ValidationError(
                        _("Product %s has no available stock.")
                        % line.product_id.display_name
                    )

                if line.qty > line.product_free_qty:
                    raise ValidationError(
                        _("Issued quantity (%s) cannot exceed available quantity (%s) for product %s.")
                        % (
                            line.qty,
                            line.product_free_qty,
                            line.product_id.display_name,
                        )
                    )
                move_vals_list.append(
                    {
                        "name": rec.name,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.qty,
                        "product_uom": line.product_id.uom_id.id,
                        "location_id": stock_location.id,
                        "location_dest_id": job_work_location.id,
                        "company_id": self.env.company.id,
                    }
                )
                line.remaining_qty = line.qty

            moves = self.env["stock.move"].create(move_vals_list)
            moves._action_confirm()
            moves._action_assign()
            for move in moves:
                move.quantity = move.product_uom_qty
                move.picked = True
            moves._action_done()
            rec.state = "confirmed"
            rec._create_auto_receipt_if_missing()

    @api.depends('line_ids.price_subtotal', 'line_ids.price_total')
    def _compute_totals(self):

        for rec in self:

            untaxed = sum(rec.line_ids.mapped('price_subtotal'))

            total = sum(rec.line_ids.mapped('price_total'))

            rec.amount_untaxed = untaxed
            rec.amount_tax = total - untaxed
            rec.amount_total = total
            rec.amount_due = total

    @api.depends('line_ids.tax_ids', 'line_ids.price_total', 'line_ids.price_subtotal')
    def _compute_tax_label(self):

        for rec in self:

            cgst = 0.0
            sgst = 0.0
            igst = 0.0

            for line in rec.line_ids:

                tax_amount = line.price_total - line.price_subtotal

                for tax in line.tax_ids:

                    tax_name = (tax.name or '').upper()

                    if 'IGST' in tax_name:
                        igst += tax_amount

                    elif 'CGST' in tax_name:
                        cgst += tax_amount / 2

                    elif 'SGST' in tax_name or 'UTGST' in tax_name:
                        sgst += tax_amount / 2

            if igst:
                rec.tax_label = "IGST"
                rec.tax_amount_display = str(round(igst, 2))

            elif cgst or sgst:
                rec.tax_label = f"CGST + SGST"
                rec.tax_amount_display = f"{round(cgst,2)} + {round(sgst,2)}"

            else:
                rec.tax_label = "Tax"
                rec.tax_amount_display = "0.00"

    @api.depends(
        'line_ids.price_subtotal',
        'line_ids.price_total',
        'line_ids.tax_ids'
    )
    def _compute_tax_breakup(self):

        for rec in self:

            cgst = 0.0
            sgst = 0.0
            igst = 0.0

            for line in rec.line_ids:

                taxes_data = line.tax_ids.compute_all(
                    line.price_unit,
                    quantity=line.qty,
                    currency=rec.currency_id,
                    product=line.product_id,
                    partner=rec.contractor_id,
                )

                for tax_line in taxes_data.get('taxes', []):

                    tax = self.env['account.tax'].browse(tax_line['id'])

                    tax_name = (tax.name or '').upper()

                    if 'CGST' in tax_name:
                        cgst += tax_line['amount']

                    elif 'SGST' in tax_name or 'UTGST' in tax_name:
                        sgst += tax_line['amount']

                    elif 'IGST' in tax_name:
                        igst += tax_line['amount']

            rec.cgst_amount = cgst
            rec.sgst_amount = sgst
            rec.igst_amount = igst

    def action_export_xlsx(self):

        output = io.BytesIO()

        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet("Job Work Issue")

        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D9D9D9',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter',
        })

        normal = workbook.add_format({
            'border': 1,
        })

        money = workbook.add_format({
            'border': 1,
            'num_format': '#,##0.00',
        })

        headers = [
            'Slip',
            'Contractor',
            'Issue Date',
            'Product',
            'Qty',
            'Price',
            'Tax Excl.',
            'CGST',
            'SGST',
            'IGST',
            'Grand Total',
        ]

        for col, header in enumerate(headers):
            sheet.write(0, col, header, header_format)

        row = 1

        for rec in self:

            first_line = True

            for line in rec.line_ids:

                sheet.write(
                    row,
                    0,
                    rec.name if first_line else '',
                    normal
                )

                sheet.write(
                    row,
                    1,
                    rec.contractor_id.name if first_line else '',
                    normal
                )

                sheet.write(
                    row,
                    2,
                    str(rec.issue_date or ''),
                    normal
                )

                sheet.write(
                    row,
                    3,
                    line.product_id.display_name or '',
                    normal
                )

                sheet.write(
                    row,
                    4,
                    line.qty or 0,
                    normal
                )

                sheet.write(
                    row,
                    5,
                    line.price_unit or 0,
                    money
                )

                sheet.write(
                    row,
                    6,
                    line.price_subtotal or 0,
                    money
                )

                sheet.write(
                    row,
                    7,
                    rec.cgst_amount if first_line else '',
                    money if first_line else normal
                )

                sheet.write(
                    row,
                    8,
                    rec.sgst_amount if first_line else '',
                    money if first_line else normal
                )

                sheet.write(
                    row,
                    9,
                    rec.igst_amount if first_line else '',
                    money if first_line else normal
                )

                sheet.write(
                    row,
                    10,
                    rec.amount_total if first_line else '',
                    money if first_line else normal
                )

                first_line = False
                row += 1    

        # Column widths
        sheet.set_column('A:A', 15)
        sheet.set_column('B:B', 25)
        sheet.set_column('C:C', 20)
        sheet.set_column('D:D', 30)
        sheet.set_column('E:E', 10)
        sheet.set_column('F:F', 12)
        sheet.set_column('G:K', 15)

        workbook.close()
        output.seek(0)

        file_data = base64.b64encode(output.read())

        attachment = self.env['ir.attachment'].create({
            'name': 'Job_Work_Issue.xlsx',
            'type': 'binary',
            'datas': file_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }


class JobWorkIssueLine(models.Model):
    _name = "dw.job.work.issue.line"
    _description = "Job Work Issue Line"
    _order = "issue_id, id"

    issue_id = fields.Many2one(
        "dw.job.work.issue",
        string="Issue Slip",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Raw Material",
        required=True,
    )
    product_free_qty = fields.Float(
        string="Free Quantity",
        compute="_compute_product_free_qty",
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )
    qty = fields.Float(string="Issued Quantity", required=True)
    remaining_qty = fields.Float(
        string="Remaining Quantity",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(related="issue_id.state", store=True, readonly=True)
    issue_date = fields.Datetime(related="issue_id.issue_date", store=True, readonly=True)
    price_unit = fields.Float(
        string="Price"
    )

    tax_ids = fields.Many2many(
        "account.tax",
        string="Taxes",
        domain=[('type_tax_use', '=', 'sale')]
    )

    price_subtotal = fields.Monetary(
        string="Tax Excl.",
        compute="_compute_amount",
        store=True,
    )

    price_total = fields.Monetary(
        string="Tax Incl.",
        compute="_compute_amount",
        store=True,
    )

    currency_id = fields.Many2one(
        "res.currency",
        related="issue_id.company_id.currency_id",
        readonly=True,
    )

    company_id = fields.Many2one(
        "res.company",
        related="issue_id.company_id",
        readonly=True,
    )

    @api.onchange('product_id')
    def _onchange_product_validation(self):
        for rec in self:
            if rec.product_id and rec.product_free_qty <= 0:
                product = rec.product_id
                rec.product_id = False

                return {
                    'warning': {
                        'title': _('Insufficient Stock'),
                        'message': _(
                            '%s has no free quantity available.'
                        ) % product.display_name
                    }
                }

    @api.constrains('qty', 'product_id')
    def _check_free_qty(self):
        for rec in self:
            if rec.product_id and rec.qty > rec.product_free_qty:
                raise ValidationError(
                    _(
                        "Issued Quantity (%s) cannot be greater than Free Quantity (%s) for product %s."
                    ) % (
                        rec.qty,
                        rec.product_free_qty,
                        rec.product_id.display_name
                    )
                )
        
    @api.onchange('product_id', 'issue_id.contractor_id')
    def _onchange_product_id_set_taxes(self):

        AccountFiscalPosition = self.env['account.fiscal.position']

        for line in self:

            if not line.product_id:
                line.tax_ids = False
                continue

            company = line.issue_id.company_id
            partner = line.issue_id.contractor_id

            # Product customer taxes
            taxes = line.product_id.taxes_id.filtered(
                lambda t: t.company_id == company
            )

            # Get fiscal position from partner
            fiscal_position = AccountFiscalPosition._get_fiscal_position(
                partner=partner
            )

            # Automatically map taxes
            taxes = fiscal_position.map_tax(taxes)

            line.tax_ids = [(6, 0, taxes.ids)]

    @api.depends("product_id")
    def _compute_product_free_qty(self):
        stock_location = self.env.ref("stock.stock_location_stock", raise_if_not_found=False)
        for rec in self:
            rec.product_free_qty = 0.0
            if not rec.product_id:
                continue
            product = rec.product_id
            if stock_location:
                product = product.with_context(location=stock_location.id)
            rec.product_free_qty = product.free_qty

    @api.constrains("qty")
    def _check_qty(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError(_("Issued quantity must be greater than zero."))

    @api.constrains("issue_id", "product_id")
    def _check_duplicate_product(self):
        for rec in self:
            if not rec.issue_id or not rec.product_id:
                continue
            duplicates = rec.issue_id.line_ids.filtered(
                lambda line: line.product_id == rec.product_id and line.id != rec.id
            )
            if duplicates:
                raise ValidationError(
                    _("Duplicate raw material products are not allowed in Issue.")
                )
            
    @api.depends('qty', 'price_unit', 'tax_ids')
    def _compute_amount(self):
        for line in self:

            taxes = line.tax_ids.compute_all(
                line.price_unit,
                quantity=line.qty,
                currency=line.currency_id,
                product=line.product_id,
                partner=line.issue_id.contractor_id,
            )

            line.price_subtotal = taxes['total_excluded']
            line.price_total = taxes['total_included']
