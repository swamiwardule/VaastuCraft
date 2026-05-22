from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class JobWorkReceipt(models.Model):
    _name = "dw.job.work.receipt"
    _description = "Job Work Receipt"
    _order = "receipt_date desc, id desc"

    receipt_date = fields.Datetime(
        string="Receipt Date",
        default=fields.Datetime.now,
        required=True,
    )
    contractor_id = fields.Many2one(
        "res.partner",
        string="Contractor",
        required=True,
    )
    issue_id = fields.Many2one(
        "dw.job.work.issue",
        string="Issue Slip",
        copy=False,
        readonly=True,
        ondelete="set null",
    )
    raw_material_product_id = fields.Many2one(
        "product.product",
        string="Raw Material",
    )
    available_qty = fields.Float(
        string="Available Raw Material",
        compute="_compute_available_qty",
        readonly=True,
    )
    qty_used = fields.Float(string="Raw Material Used")
    raw_line_ids = fields.One2many(
        "dw.job.work.receipt.raw.line",
        "receipt_id",
        string="Raw Materials",
        copy=True,
    )
    line_ids = fields.One2many(
        "dw.job.work.receipt.line",
        "receipt_id",
        string="Finished Products",
        copy=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed")],
        default="draft",
        required=True,
        readonly=True,
        copy=False,
    )
    status_display = fields.Char(
        string="Status",
        compute="_compute_status_display",
    )

    _sql_constraints = [
        (
            "unique_receipt_issue",
            "unique(issue_id)",
            "Only one receipt can be linked to an issue slip.",
        )
    ]

    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
        store=True,
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

    @api.depends('line_ids.price_subtotal', 'line_ids.price_total')
    def _compute_totals(self):

        for rec in self:

            untaxed = sum(rec.line_ids.mapped('price_subtotal'))
            total = sum(rec.line_ids.mapped('price_total'))

            rec.amount_untaxed = untaxed
            rec.amount_tax = total - untaxed
            rec.amount_total = total

    @api.depends(
        'line_ids.tax_ids',
        'line_ids.price_subtotal',
        'line_ids.price_total'
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

                    name = (tax.name or '').upper()

                    if 'CGST' in name:
                        cgst += tax_line['amount']

                    elif 'SGST' in name or 'UTGST' in name:
                        sgst += tax_line['amount']

                    elif 'IGST' in name:
                        igst += tax_line['amount']

            rec.cgst_amount = cgst
            rec.sgst_amount = sgst
            rec.igst_amount = igst

    @api.depends("state")
    def _compute_status_display(self):
        for rec in self:
            rec.status_display = "Pending" if rec.state == "draft" else "Done"

    @api.depends("contractor_id", "raw_material_product_id")
    def _compute_available_qty(self):
        line_model = self.env["dw.job.work.issue.line"]
        for rec in self:
            rec.available_qty = 0.0
            if not rec.contractor_id or not rec.raw_material_product_id:
                continue
            grouped = line_model.read_group(
                [
                    ("issue_id.contractor_id", "=", rec.contractor_id.id),
                    ("issue_id.state", "=", "confirmed"),
                    ("product_id", "=", rec.raw_material_product_id.id),
                    ("remaining_qty", ">", 0),
                ],
                ["remaining_qty:sum"],
                [],
            )
            rec.available_qty = grouped[0]["remaining_qty"] if grouped else 0.0

    @api.constrains("qty_used")
    def _check_positive_quantities(self):
        for rec in self:
            if rec.raw_material_product_id and rec.qty_used <= 0:
                raise ValidationError(_("Raw material used must be greater than zero."))

    def _check_duplicate_receipt_products(self):
        for rec in self:
            raw_seen_product_ids = set()
            raw_duplicate_names = []
            for line in rec.raw_line_ids.filtered(lambda l: l.product_id):
                if line.product_id.id in raw_seen_product_ids:
                    raw_duplicate_names.append(line.product_id.display_name)
                    continue
                raw_seen_product_ids.add(line.product_id.id)

            finished_seen_product_ids = set()
            finished_duplicate_names = []
            for line in rec.line_ids.filtered(lambda l: l.product_id):
                if line.product_id.id in finished_seen_product_ids:
                    finished_duplicate_names.append(line.product_id.display_name)
                    continue
                finished_seen_product_ids.add(line.product_id.id)

            if raw_duplicate_names:
                raise ValidationError(
                    _(
                        "Duplicate raw material products are not allowed in Receipt.\n"
                        "Duplicate product(s): %(products)s",
                        products=", ".join(sorted(set(raw_duplicate_names))),
                    )
                )

            if finished_duplicate_names:
                raise ValidationError(
                    _(
                        "Duplicate finished products are not allowed in Receipt.\n"
                        "Duplicate product(s): %(products)s",
                        products=", ".join(sorted(set(finished_duplicate_names))),
                    )
                )

    def _get_raw_material_consumptions(self):
        self.ensure_one()
        if self.raw_line_ids:
            return [
                {
                    "product": line.product_id,
                    "qty_used": line.qty_used,
                    "available_qty": line.available_qty,
                }
                for line in self.raw_line_ids
                if line.product_id and line.qty_used > 0
            ]
        if self.raw_material_product_id and self.qty_used > 0:
            return [
                {
                    "product": self.raw_material_product_id,
                    "qty_used": self.qty_used,
                    "available_qty": self.available_qty,
                }
            ]
        return []

    def _consume_raw_material(self, contractor, product, qty_used):
        self.ensure_one()
        fifo_lines = self.env["dw.job.work.issue.line"].search(
            [
                ("issue_id.contractor_id", "=", contractor.id),
                ("issue_id.state", "=", "confirmed"),
                ("product_id", "=", product.id),
                ("remaining_qty", ">", 0),
            ],
            order="issue_date asc, id asc",
        )

        remaining_to_consume = qty_used
        for line in fifo_lines:
            if remaining_to_consume <= 0:
                break
            consume_now = min(line.remaining_qty, remaining_to_consume)
            line.remaining_qty -= consume_now
            remaining_to_consume -= consume_now

        if remaining_to_consume > 0:
            raise ValidationError(
                _("Not enough %(product)s material available to complete this receipt.", product=product.display_name)
            )

    def action_confirm(self):
        inventory_location = self.env["stock.location"].sudo().search(
            [("usage", "=", "inventory"), ("company_id", "in", [self.env.company.id, False])],
            limit=1,
        )
        if not inventory_location:
            inventory_location = self.env.ref("stock.location_inventory", raise_if_not_found=False)
        job_work_location = self.env.ref("DW_JOB_WORK.job_work_location")
        stock_location = self.env.ref("stock.stock_location_stock")

        for rec in self:
            if rec.state != "draft":
                continue
            rec._check_duplicate_receipt_products()
            raw_consumptions = rec._get_raw_material_consumptions()
            if not raw_consumptions:
                raise ValidationError(_("Add at least one raw material line before confirming."))
            if not rec.line_ids:
                raise ValidationError(_("Add at least one finished product line before confirming."))
            for raw in raw_consumptions:
                if raw["qty_used"] > raw["available_qty"]:
                    raise ValidationError(
                        _(
                            "Cannot consume %(used)s because only %(available)s of %(product)s is available for %(contractor)s.",
                            used=raw["qty_used"],
                            available=raw["available_qty"],
                            product=raw["product"].display_name,
                            contractor=rec.contractor_id.display_name,
                        )
                    )

            raw_moves = self.env["stock.move"]
            for raw in raw_consumptions:
                rec._consume_raw_material(rec.contractor_id, raw["product"], raw["qty_used"])
                raw_move = self.env["stock.move"].create(
                    {
                        "name": _("%s Raw Consumption", rec.contractor_id.display_name),
                        "product_id": raw["product"].id,
                        "product_uom_qty": raw["qty_used"],
                        "product_uom": raw["product"].uom_id.id,
                        "location_id": job_work_location.id,
                        "location_dest_id": inventory_location.id,
                        "company_id": self.env.company.id,
                        "is_inventory": True,
                    }
                )
                raw_moves |= raw_move
            raw_moves._action_confirm()
            for move in raw_moves:
                move.quantity = move.product_uom_qty
                move.picked = True
            raw_moves._action_done()

            finished_moves = self.env["stock.move"]
            for line in rec.line_ids:
                finished_move = self.env["stock.move"].create(
                    {
                        "name": _("%s Finished Receipt", rec.contractor_id.display_name),
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.qty,
                        "product_uom": line.product_uom_id.id,
                        "location_id": inventory_location.id,
                        "location_dest_id": stock_location.id,
                        "company_id": self.env.company.id,
                        "is_inventory": True,
                    }
                )
                finished_moves |= finished_move
            finished_moves._action_confirm()
            for move in finished_moves:
                move.quantity = move.product_uom_qty
                move.picked = True
            finished_moves._action_done()
            rec.state = "confirmed"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_duplicate_receipt_products()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._check_duplicate_receipt_products()
        return res


class JobWorkReceiptLine(models.Model):
    _name = "dw.job.work.receipt.line"
    _description = "Job Work Receipt Line"
    _order = "receipt_id, id"

    receipt_id = fields.Many2one(
        "dw.job.work.receipt",
        string="Receipt",
        required=True,
        ondelete="cascade",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Finished Product",
        required=True,
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )
    qty = fields.Float(string="Finished Quantity", required=True)
    state = fields.Selection(related="receipt_id.state", store=True, readonly=True)
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
        related="receipt_id.currency_id",
        readonly=True,
    )

    @api.depends('qty', 'price_unit', 'tax_ids')
    def _compute_amount(self):

        for line in self:

            taxes = line.tax_ids.compute_all(
                line.price_unit,
                quantity=line.qty,
                currency=line.currency_id,
                product=line.product_id,
                partner=line.receipt_id.contractor_id,
            )

            line.price_subtotal = taxes['total_excluded']
            line.price_total = taxes['total_included']

    @api.onchange('product_id', 'receipt_id.contractor_id')
    def _onchange_product_id_set_taxes(self):

        AccountFiscalPosition = self.env['account.fiscal.position']

        for line in self:

            if not line.product_id:
                line.tax_ids = False
                continue

            company = line.receipt_id.company_id
            partner = line.receipt_id.contractor_id

            taxes = line.product_id.taxes_id.filtered(
                lambda t: not t.company_id or t.company_id == company
            )

            fiscal_position = AccountFiscalPosition._get_fiscal_position(
                partner=partner
            )

            if fiscal_position:
                taxes = fiscal_position.map_tax(taxes)

            line.tax_ids = [(6, 0, taxes.ids)]

    @api.constrains("qty")
    def _check_positive_qty(self):
        for rec in self:
            if rec.qty <= 0:
                raise ValidationError(_("Finished quantity must be greater than zero."))

    @api.constrains("receipt_id", "product_id")
    def _check_duplicate_product(self):
        for rec in self:
            if not rec.receipt_id or not rec.product_id:
                continue
            duplicates = rec.receipt_id.line_ids.filtered(
                lambda line: line.product_id == rec.product_id and line.id != rec.id
            )
            if duplicates:
                raise ValidationError(
                    _("Duplicate finished products are not allowed in Receipt.")
                )


class JobWorkReceiptRawLine(models.Model):
    _name = "dw.job.work.receipt.raw.line"
    _description = "Job Work Receipt Raw Material Line"
    _order = "receipt_id, id"

    receipt_id = fields.Many2one(
        "dw.job.work.receipt",
        string="Receipt",
        required=True,
        ondelete="cascade",
    )
    contractor_id = fields.Many2one(related="receipt_id.contractor_id", store=True, readonly=True)
    product_id = fields.Many2one(
        "product.product",
        string="Raw Material",
        required=True,
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="Unit",
        related="product_id.uom_id",
        store=True,
        readonly=True,
    )
    available_qty = fields.Float(
        string="Available Raw Material",
        compute="_compute_available_qty",
        readonly=True,
    )
    qty_used = fields.Float(string="Raw Material Used")
    state = fields.Selection(related="receipt_id.state", store=True, readonly=True)

    @api.depends("receipt_id.contractor_id", "product_id")
    def _compute_available_qty(self):
        line_model = self.env["dw.job.work.issue.line"]
        for rec in self:
            rec.available_qty = 0.0
            if not rec.contractor_id or not rec.product_id:
                continue
            grouped = line_model.read_group(
                [
                    ("issue_id.contractor_id", "=", rec.contractor_id.id),
                    ("issue_id.state", "=", "confirmed"),
                    ("product_id", "=", rec.product_id.id),
                    ("remaining_qty", ">", 0),
                ],
                ["remaining_qty:sum"],
                [],
            )
            rec.available_qty = grouped[0]["remaining_qty"] if grouped else 0.0

    @api.constrains("qty_used")
    def _check_positive_qty(self):
        for rec in self:
            if rec.qty_used < 0:
                raise ValidationError(_("Raw material used must be greater than zero."))

    @api.constrains("receipt_id", "product_id")
    def _check_duplicate_product(self):
        for rec in self:
            if not rec.receipt_id or not rec.product_id:
                continue
            duplicates = rec.receipt_id.raw_line_ids.filtered(
                lambda line: line.product_id == rec.product_id and line.id != rec.id
            )
            if duplicates:
                raise ValidationError(
                    _("Duplicate raw material products are not allowed in Receipt.")
                )
