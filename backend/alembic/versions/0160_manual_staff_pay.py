"""Private manual staff pay, immutable disbursements and revision history.

Revision ID: 0160
Revises: 0159
"""

import re

from alembic import op

revision = "0160"
down_revision = "0159"
branch_labels = None
depends_on = None


def _sql(sql: str) -> None:
    start = 0
    for match in re.finditer(r"\$\$.*?\$\$|'(?:''|[^'])*'|;", sql, flags=re.DOTALL):
        if match.group() == ";":
            statement = sql[start : match.start()].strip()
            if statement:
                op.execute(statement)
            start = match.end()
    if sql[start:].strip():
        op.execute(sql[start:])


ACCESS_SQL = r"""
CREATE FUNCTION public.staff_pay_member(p_tenant UUID) RETURNS BOOLEAN
LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT SESSION_USER='aurum_app' AND NOT public.is_support_session() AND NOT public.is_tenant_support_session()
   AND p_tenant=public.current_tenant_id() AND EXISTS(
     SELECT 1 FROM public.tenant_membership m JOIN public.app_user u ON u.id=m.user_id
     JOIN public.session s ON s.user_id=u.id
     WHERE m.tenant_id=p_tenant AND m.user_id=public.current_app_user_id()
       AND m.status='active' AND u.status='active'
       AND NOT u.is_developer AND NOT u.is_administrator
       AND s.id=NULLIF(current_setting('app.auth_session_id',true),'')::UUID
       AND s.revoked_at IS NULL AND s.expires_at>statement_timestamp())
$$;
CREATE FUNCTION public.staff_pay_manager(p_tenant UUID,p_write BOOLEAN) RETURNS BOOLEAN
LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT public.staff_pay_member(p_tenant) AND EXISTS(
   SELECT 1 FROM public.permission permission WHERE permission.is_active
   AND (permission.code='staff_pay.manage' OR (NOT p_write AND permission.code='staff_pay.view'))
   AND (public.tenant_actor_is_owner(p_tenant) OR EXISTS(
     SELECT 1 FROM public.tenant_membership m
     JOIN public.user_assignment a ON a.membership_id=m.id AND a.tenant_id=m.tenant_id
       AND a.user_id=m.user_id AND a.is_active AND a.branch_id IS NULL
     JOIN public.role r ON r.id=a.role_id AND r.is_active
       AND (r.tenant_id IS NULL OR r.tenant_id=p_tenant)
     JOIN public.access_role_version v ON v.id=a.role_version_id AND v.role_id=r.id AND v.status='published'
     JOIN public.access_role_version_permission vp ON vp.role_version_id=v.id
     WHERE m.tenant_id=p_tenant AND m.user_id=public.current_app_user_id() AND m.status='active'
       AND vp.permission_code=permission.code)))
$$;
CREATE FUNCTION public.staff_pay_can_read(p_tenant UUID,p_employee UUID) RETURNS BOOLEAN
LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT public.staff_pay_member(p_tenant) AND
   (p_employee=public.current_app_user_id() OR public.staff_pay_manager(p_tenant,false))
$$;
CREATE FUNCTION public.staff_pay_employees(p_tenant UUID)
RETURNS TABLE(id UUID,full_name TEXT,status TEXT)
LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT m.user_id,m.full_name,m.status FROM public.tenant_membership m
 JOIN public.app_user u ON u.id=m.user_id
 WHERE public.staff_pay_manager(p_tenant,false) AND m.tenant_id=p_tenant
   AND m.status<>'pending' AND NOT u.is_developer AND NOT u.is_administrator
$$;
"""


COMMAND_SQL = r"""
CREATE FUNCTION public.command_staff_pay(p_action TEXT,p_period UUID,p_entry UUID,
 p_operation UUID,p_payload JSONB) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE
 v_tenant UUID:=public.current_tenant_id(); v_actor UUID:=public.current_app_user_id();
 v_period public.staff_pay_period%ROWTYPE; v_entry public.staff_pay_entry%ROWTYPE;
 v_membership UUID; v_employee UUID; v_from DATE; v_to DATE; v_amount NUMERIC;
 v_today DATE; v_date DATE; v_note TEXT; v_id UUID; v_existing JSONB; v_author UUID;
 v_kind TEXT; v_reverse UUID; v_timezone TEXT;
BEGIN
 PERFORM 1 FROM public.tenant WHERE id=v_tenant FOR SHARE;
 PERFORM 1 FROM public.session WHERE id=NULLIF(current_setting('app.auth_session_id',true),'')::UUID
   AND user_id=v_actor AND revoked_at IS NULL AND expires_at>statement_timestamp() FOR SHARE;
 IF NOT COALESCE(public.staff_pay_manager(v_tenant,true),false) THEN
   RAISE EXCEPTION 'staff_pay_denied' USING ERRCODE='42501'; END IF;
 IF p_action IS NULL OR p_action NOT IN ('create','update','entry','reverse')
   OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' THEN
   RAISE EXCEPTION 'staff_pay_invalid' USING ERRCODE='22023'; END IF;
 IF p_action<>'update' THEN
   IF p_operation IS NULL THEN RAISE EXCEPTION 'staff_pay_operation_required'
     USING ERRCODE='22023'; END IF;
   PERFORM pg_advisory_xact_lock(hashtextextended(v_tenant::TEXT||p_operation::TEXT,160));
   SELECT id,create_payload,created_by INTO v_id,v_existing,v_author
     FROM public.staff_pay_period WHERE tenant_id=v_tenant AND operation_id=p_operation;
   IF FOUND THEN
     IF p_action<>'create' OR v_existing<>p_payload OR v_author<>v_actor THEN
       RAISE EXCEPTION 'staff_pay_operation_conflict' USING ERRCODE='23505'; END IF;
     RETURN v_id;
   END IF;
   SELECT id,request_payload,recorded_by INTO v_id,v_existing,v_author
     FROM public.staff_pay_entry WHERE tenant_id=v_tenant AND operation_id=p_operation;
   IF FOUND THEN
     IF p_action NOT IN ('entry','reverse') OR v_existing<>
       jsonb_build_object('action',p_action,'period_id',p_period,'entry_id',p_entry,
         'payload',p_payload) OR v_author<>v_actor THEN
       RAISE EXCEPTION 'staff_pay_operation_conflict' USING ERRCODE='23505'; END IF;
     RETURN v_id;
   END IF;
 END IF;
 SELECT COALESCE(report_timezone,'Asia/Dushanbe') INTO v_timezone
   FROM public.tenant_settings WHERE tenant_id=v_tenant;
 v_today:=(statement_timestamp() AT TIME ZONE COALESCE(v_timezone,'Asia/Dushanbe'))::DATE;
 IF p_action IN ('create','update','entry') THEN
   IF COALESCE(p_payload->>'amount','') !~ '^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$' THEN
     RAISE EXCEPTION 'staff_pay_invalid_amount' USING ERRCODE='22023'; END IF;
   v_amount:=(p_payload->>'amount')::NUMERIC;
   v_note:=p_payload->>'note';
   IF v_note IS NULL OR length(v_note)>1000 THEN
     RAISE EXCEPTION 'staff_pay_invalid_note' USING ERRCODE='22023'; END IF;
 END IF;
 IF p_action='create' THEN
   v_employee:=(p_payload->>'employee_id')::UUID;
   v_from:=(p_payload->>'period_from')::DATE; v_to:=(p_payload->>'period_to')::DATE;
   IF v_from IS NULL OR v_to IS NULL OR NOT isfinite(v_from) OR NOT isfinite(v_to)
     OR v_from>v_to THEN RAISE EXCEPTION 'staff_pay_invalid_period' USING ERRCODE='22023';
   END IF;
   SELECT m.id INTO v_membership FROM public.tenant_membership m
     JOIN public.app_user u ON u.id=m.user_id
     WHERE m.tenant_id=v_tenant AND m.user_id=v_employee AND m.status<>'pending'
       AND NOT u.is_developer AND NOT u.is_administrator FOR UPDATE OF m;
   IF NOT FOUND THEN RAISE EXCEPTION 'staff_pay_employee_not_found' USING ERRCODE='P0002'; END IF;
   IF EXISTS(SELECT 1 FROM public.staff_pay_period WHERE tenant_id=v_tenant
     AND employee_id=v_employee AND period_from<=v_to AND period_to>=v_from) THEN
     RAISE EXCEPTION 'staff_pay_period_overlap' USING ERRCODE='23505'; END IF;
   INSERT INTO public.staff_pay_period(tenant_id,employee_id,membership_id,period_from,
     period_to,amount,note,operation_id,create_payload,created_by,updated_by)
   VALUES(v_tenant,v_employee,v_membership,v_from,v_to,v_amount,v_note,p_operation,
     p_payload,v_actor,v_actor) RETURNING id INTO v_id;
   RETURN v_id;
 END IF;
 SELECT * INTO v_period FROM public.staff_pay_period WHERE tenant_id=v_tenant
   AND id=p_period FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'staff_pay_period_not_found' USING ERRCODE='P0002'; END IF;
 IF p_action='update' THEN
   IF (p_payload->>'expected_row_version')::INTEGER IS DISTINCT FROM v_period.row_version THEN
     RAISE EXCEPTION 'staff_pay_revision_conflict' USING ERRCODE='40001'; END IF;
   IF v_period.amount=v_amount AND v_period.note=v_note THEN RETURN p_period; END IF;
   UPDATE public.staff_pay_period SET amount=v_amount,note=v_note,row_version=row_version+1,
     updated_at=statement_timestamp(),updated_by=v_actor WHERE id=p_period;
   RETURN p_period;
 END IF;
 IF p_action='entry' THEN
   v_kind:=p_payload->>'kind'; v_date:=(p_payload->>'paid_on')::DATE;
   IF v_kind IS NULL OR v_kind NOT IN ('payment','advance') OR v_amount<=0 OR v_date IS NULL
     OR NOT isfinite(v_date) OR v_date>v_today THEN
     RAISE EXCEPTION 'staff_pay_invalid_entry' USING ERRCODE='22023'; END IF;
 ELSE
   v_note:=p_payload->>'reason';
   IF v_note IS NULL OR length(btrim(v_note)) NOT BETWEEN 3 AND 1000 THEN
     RAISE EXCEPTION 'staff_pay_reason_required' USING ERRCODE='22023'; END IF;
   SELECT * INTO v_entry FROM public.staff_pay_entry WHERE tenant_id=v_tenant
     AND period_id=p_period AND id=p_entry;
   IF NOT FOUND OR v_entry.kind='reversal' THEN
     RAISE EXCEPTION 'staff_pay_entry_not_found' USING ERRCODE='P0002'; END IF;
   IF EXISTS(SELECT 1 FROM public.staff_pay_entry WHERE tenant_id=v_tenant
     AND reverses_entry_id=p_entry) THEN
     RAISE EXCEPTION 'staff_pay_already_reversed' USING ERRCODE='23505'; END IF;
   v_kind:='reversal'; v_amount:=v_entry.amount; v_date:=v_today; v_reverse:=p_entry;
 END IF;
 INSERT INTO public.staff_pay_entry(tenant_id,period_id,kind,amount,paid_on,note,
   reverses_entry_id,recorded_by,operation_id,request_payload)
 VALUES(v_tenant,p_period,v_kind,v_amount,v_date,v_note,v_reverse,v_actor,p_operation,
   jsonb_build_object('action',p_action,'period_id',p_period,'entry_id',p_entry,
     'payload',p_payload)) RETURNING id INTO v_id;
 RETURN v_id;
END $$;
"""


def upgrade() -> None:
    op.execute("CREATE TEMP TABLE staff_pay_reference_grants(name TEXT) ON COMMIT DROP")
    for table in ("tenant", "app_user", "tenant_membership"):
        op.execute(f"""DO $$ BEGIN
          IF NOT has_table_privilege('aurum_schema_owner','public.{table}','REFERENCES') THEN
            INSERT INTO pg_temp.staff_pay_reference_grants VALUES('{table}');
            GRANT REFERENCES ON public.{table} TO aurum_schema_owner;
          END IF; END $$""")
    _sql(r"""
    INSERT INTO public.permission(code,group_code,name,description,min_level_required,
      scope_type,target_role_type,risk_level,owner_grantable,owner_delegable)
    VALUES('staff_pay.view','staff_pay','Просмотр выплат сотрудникам',
      'Суммы и история ручных выплат всей аптеки',4,'TENANT_ALL','tenant','sensitive',true,true),
      ('staff_pay.manage','staff_pay','Управление выплатами сотрудникам',
      'Ручные суммы, выдачи и отмены всей аптеки',4,'TENANT_ALL','tenant','sensitive',true,true);
    ALTER TABLE public.role_permission DISABLE TRIGGER trg_guard_role_permission_mutation;
    INSERT INTO public.role_permission(role_id,permission_code)
      SELECT r.id,p.code FROM public.role r CROSS JOIN public.permission p
      WHERE r.is_active AND r.is_protected AND r.protected_kind='tenant_owner'
        AND p.code IN ('staff_pay.view','staff_pay.manage') ON CONFLICT DO NOTHING;
    ALTER TABLE public.role_permission ENABLE TRIGGER trg_guard_role_permission_mutation;
    INSERT INTO public.role_template_permission(template_id,permission_code)
      SELECT r.id,p.code FROM public.role_template r CROSS JOIN public.permission p
      WHERE r.slug='owner' AND r.is_active AND p.code IN ('staff_pay.view','staff_pay.manage')
      ON CONFLICT DO NOTHING;
    INSERT INTO public.access_role_version_permission(role_version_id,permission_code)
      SELECT v.id,p.code FROM public.access_role_version v JOIN public.role r ON r.id=v.role_id
      CROSS JOIN public.permission p WHERE v.status='published' AND r.is_active
        AND r.is_protected AND r.protected_kind='tenant_owner'
        AND p.code IN ('staff_pay.view','staff_pay.manage') ON CONFLICT DO NOTHING;
    SELECT public.bump_all_authorization_policy_revisions();
    CREATE TABLE public.staff_pay_period(
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL REFERENCES public.tenant(id),
      employee_id UUID NOT NULL,membership_id UUID NOT NULL REFERENCES public.tenant_membership(id),
      period_from DATE NOT NULL,period_to DATE NOT NULL CHECK(period_to>=period_from),
      amount NUMERIC(14,2) NOT NULL CHECK(amount>=0 AND amount<>'NaN'::NUMERIC),
      note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=1000),row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>0),
      operation_id UUID NOT NULL,create_payload JSONB NOT NULL,
      created_by UUID NOT NULL REFERENCES public.app_user(id),updated_by UUID NOT NULL REFERENCES public.app_user(id),
      created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
      UNIQUE(tenant_id,id),UNIQUE(tenant_id,operation_id),
      FOREIGN KEY(tenant_id,employee_id) REFERENCES public.tenant_membership(tenant_id,user_id));
    CREATE INDEX ix_staff_pay_period_employee ON public.staff_pay_period(tenant_id,employee_id,period_from DESC,id);
    CREATE TABLE public.staff_pay_entry(
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,period_id UUID NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('payment','advance','reversal')),
      amount NUMERIC(14,2) NOT NULL CHECK(amount>0 AND amount<>'NaN'::NUMERIC),paid_on DATE NOT NULL,
      note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=1000),reverses_entry_id UUID,
      recorded_by UUID NOT NULL REFERENCES public.app_user(id),operation_id UUID NOT NULL,request_payload JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
      UNIQUE(tenant_id,id),UNIQUE(tenant_id,operation_id),UNIQUE(tenant_id,reverses_entry_id),
      FOREIGN KEY(tenant_id,period_id) REFERENCES public.staff_pay_period(tenant_id,id),
      FOREIGN KEY(tenant_id,reverses_entry_id) REFERENCES public.staff_pay_entry(tenant_id,id),
      CHECK((kind='reversal')=(reverses_entry_id IS NOT NULL)));
    CREATE INDEX ix_staff_pay_entry_period ON public.staff_pay_entry(tenant_id,period_id,created_at DESC,id);
    CREATE TABLE public.staff_pay_revision(
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL,period_id UUID NOT NULL,
      amount NUMERIC(14,2) NOT NULL,note TEXT NOT NULL,row_version INTEGER NOT NULL,
      recorded_by UUID NOT NULL REFERENCES public.app_user(id),created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
      UNIQUE(tenant_id,period_id,row_version),
      FOREIGN KEY(tenant_id,period_id) REFERENCES public.staff_pay_period(tenant_id,id));
    """)
    for table in ("tenant", "app_user", "tenant_membership"):
        op.execute(f"""DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM pg_temp.staff_pay_reference_grants WHERE name='{table}') THEN
            REVOKE REFERENCES ON public.{table} FROM aurum_schema_owner;
          END IF; END $$""")
    op.execute("DROP TABLE pg_temp.staff_pay_reference_grants")
    _sql(ACCESS_SQL)
    for table in ("staff_pay_period", "staff_pay_entry", "staff_pay_revision"):
        _sql(f"""
          ALTER TABLE public.{table} OWNER TO aurum_schema_owner;
          ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY;
          REVOKE ALL ON public.{table} FROM PUBLIC,aurum_app,aurum_support;
          GRANT SELECT ON public.{table} TO aurum_app;
          CREATE POLICY schema_owner ON public.{table} TO aurum_schema_owner USING(true) WITH CHECK(true);
          CREATE TRIGGER a00_tenant_access BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_enforce_tenant_access('staff');
        """)
        predicate = (
            "public.staff_pay_can_read(tenant_id,employee_id)"
            if table == "staff_pay_period"
            else (
                "EXISTS(SELECT 1 FROM public.staff_pay_period p WHERE p.tenant_id="
                f"{table}.tenant_id AND p.id={table}.period_id)"
            )
        )
        op.execute(
            f"CREATE POLICY own_or_manager ON public.{table} FOR SELECT TO aurum_app USING({predicate})"
        )
    _sql(r"""
    CREATE FUNCTION public.trg_staff_pay_period_guard() RETURNS TRIGGER
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'staff_pay_period_immutable' USING ERRCODE='55000'; END IF;
      IF TG_OP='INSERT' THEN
        IF NOT EXISTS(SELECT 1 FROM public.tenant_membership WHERE id=NEW.membership_id
          AND tenant_id=NEW.tenant_id AND user_id=NEW.employee_id) THEN
          RAISE EXCEPTION 'staff_pay_membership_mismatch' USING ERRCODE='23514'; END IF;
      ELSIF (to_jsonb(NEW)-ARRAY['amount','note','row_version','updated_at','updated_by'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['amount','note','row_version','updated_at','updated_by'])
        OR NEW.row_version<>OLD.row_version+1 THEN
        RAISE EXCEPTION 'staff_pay_period_identity_immutable' USING ERRCODE='55000';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER period_guard BEFORE INSERT OR UPDATE OR DELETE ON public.staff_pay_period
      FOR EACH ROW EXECUTE FUNCTION public.trg_staff_pay_period_guard();
    CREATE FUNCTION public.trg_staff_pay_audit() RETURNS TRIGGER
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
      INSERT INTO public.audit_log(tenant_id,user_id,action,table_name,record_id,metadata,created_at)
      VALUES(NEW.tenant_id,public.current_app_user_id(),TG_OP,TG_TABLE_NAME,NEW.id,
        jsonb_build_object('private_staff_pay',true),statement_timestamp());
      IF TG_TABLE_NAME='staff_pay_period' THEN
        INSERT INTO public.staff_pay_revision(tenant_id,period_id,amount,note,row_version,recorded_by)
        VALUES(NEW.tenant_id,NEW.id,NEW.amount,NEW.note,NEW.row_version,NEW.updated_by);
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER trg_staff_pay_audit AFTER INSERT OR UPDATE ON public.staff_pay_period
      FOR EACH ROW EXECUTE FUNCTION public.trg_staff_pay_audit();
    CREATE TRIGGER trg_staff_pay_audit AFTER INSERT ON public.staff_pay_entry
      FOR EACH ROW EXECUTE FUNCTION public.trg_staff_pay_audit();
    CREATE TRIGGER immutable_entry BEFORE UPDATE OR DELETE ON public.staff_pay_entry
      FOR EACH ROW EXECUTE FUNCTION public.trg_reject_immutable_billing_financial_mutation();
    CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON public.staff_pay_revision
      FOR EACH ROW EXECUTE FUNCTION public.trg_reject_immutable_billing_financial_mutation();
    """)
    _sql(COMMAND_SQL)
    for signature in (
        "staff_pay_member(UUID)",
        "staff_pay_manager(UUID,BOOLEAN)",
        "staff_pay_can_read(UUID,UUID)",
        "staff_pay_employees(UUID)",
        "command_staff_pay(TEXT,UUID,UUID,UUID,JSONB)",
        "trg_staff_pay_audit()",
        "trg_staff_pay_period_guard()",
    ):
        _sql(
            f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner;"
            f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support;"
        )
        if not signature.startswith("trg_"):
            op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO aurum_app")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.staff_pay_period) THEN
      RAISE EXCEPTION 'Cannot downgrade with staff pay history' USING ERRCODE='55000';
      END IF; END $$""")
    op.execute("DROP FUNCTION public.command_staff_pay(TEXT,UUID,UUID,UUID,JSONB)")
    op.execute("DROP TABLE public.staff_pay_revision")
    op.execute("DROP TABLE public.staff_pay_entry")
    op.execute("DROP TABLE public.staff_pay_period")
    for signature in (
        "trg_staff_pay_audit()",
        "trg_staff_pay_period_guard()",
        "staff_pay_can_read(UUID,UUID)",
        "staff_pay_employees(UUID)",
        "staff_pay_manager(UUID,BOOLEAN)",
        "staff_pay_member(UUID)",
    ):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute(
        "ALTER TABLE public.role_permission DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    for table in ("access_role_version_permission", "role_template_permission", "role_permission"):
        op.execute(
            f"DELETE FROM public.{table} WHERE permission_code IN ('staff_pay.view','staff_pay.manage')"
        )
    op.execute(
        "ALTER TABLE public.role_permission ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("DELETE FROM public.permission WHERE code IN ('staff_pay.view','staff_pay.manage')")
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")
