from django.db import migrations


def forwards(apps, schema_editor):
    """Move the old paid-in/paid-out movements into the signed cash ledger, backfill cash sale and
    refund ledger rows for existing sessions, and attach registers/employees to old sessions."""
    CashSession = apps.get_model("payments", "CashSession")
    Movement = apps.get_model("payments", "CashMovement")
    Txn = apps.get_model("payments", "CashTransaction")
    Payment = apps.get_model("payments", "Payment")
    Refund = apps.get_model("payments", "Refund")
    Register = apps.get_model("payments", "Register")
    for s in CashSession.objects.all():
        if s.device_id and s.register_id is None:
            reg = Register.objects.filter(device_id=s.device_id).first() or Register.objects.create(
                store_id=s.store_id, device_id=s.device_id, code=f"R{s.device_id}", name=f"Register {s.device_id}")
            s.register_id = reg.id
        if s.employee_id is None:
            s.employee_id = s.opened_by_id
        CashSession.objects.filter(pk=s.pk).update(register_id=s.register_id, employee_id=s.employee_id)
    for m in Movement.objects.all():
        Txn.objects.create(session_id=m.session_id, kind="addition" if m.kind == "paid_in" else "paid_out",
                           amount_cents=m.amount_cents if m.kind == "paid_in" else -m.amount_cents, reason=m.reason,
                           user_id=m.user_id, authorized_by_id=m.authorized_by_id, created_at=m.created_at)
    for p in Payment.objects.filter(method="cash", cash_session__isnull=False,
                                    status__in=["captured", "partially_refunded", "refunded"]):
        Txn.objects.create(session_id=p.cash_session_id, kind="sale", amount_cents=p.amount_cents + (p.tip_cents or 0),
                           order_id=p.order_id, payment_id=p.id, user_id=p.created_by_id or CashSession.objects.get(
                               pk=p.cash_session_id).opened_by_id, reason="backfill")
    for r in Refund.objects.filter(status="succeeded", cash_session__isnull=False, payment__method="cash"):
        Txn.objects.create(session_id=r.cash_session_id, kind="refund", amount_cents=-r.amount_cents, refund_id=r.id,
                           payment_id=r.payment_id, user_id=r.requested_by_id, reason=r.reason[:200])


class Migration(migrations.Migration):
    dependencies = [("payments", "0002_cash_management")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
