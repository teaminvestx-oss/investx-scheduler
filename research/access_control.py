import logging
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

from research.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


def _next_month_first() -> date:
    today = date.today()
    return today.replace(day=1) + relativedelta(months=1)


def register_user_if_new(telegram_id: int, username: str, first_name: str) -> dict:
    sb = get_supabase_client()
    result = sb.table("users").select("*").eq("telegram_id", telegram_id).execute()

    if result.data:
        return result.data[0]

    new_user = {
        "telegram_id": telegram_id,
        "username": username or "",
        "first_name": first_name or "",
        "tier": "free",
        "queries_this_month": 0,
        "queries_limit": 10,
        "reset_date": _next_month_first().isoformat(),
    }
    insert_result = sb.table("users").insert(new_user).execute()
    logger.info(f"Registered new user: {telegram_id} ({username})")
    return insert_result.data[0]


def _maybe_reset(sb, user: dict) -> dict:
    reset_date = date.fromisoformat(str(user["reset_date"]))
    today = date.today()

    if reset_date <= today:
        next_reset = _next_month_first()
        sb.table("users").update({
            "queries_this_month": 0,
            "reset_date": next_reset.isoformat(),
        }).eq("telegram_id", user["telegram_id"]).execute()
        user = {**user, "queries_this_month": 0, "reset_date": next_reset.isoformat()}

    return user


def check_and_consume_query(telegram_id: int) -> tuple[bool, dict]:
    """
    Verify the user can query, consume one slot, and return (allowed, updated_user).
    Returns (False, user) when the monthly limit is reached.
    """
    sb = get_supabase_client()
    result = sb.table("users").select("*").eq("telegram_id", telegram_id).execute()

    if not result.data:
        return False, {}

    user = _maybe_reset(sb, result.data[0])

    if user["queries_this_month"] >= user["queries_limit"]:
        return False, user

    new_count = user["queries_this_month"] + 1
    sb.table("users").update({
        "queries_this_month": new_count,
        "last_query_at": datetime.utcnow().isoformat(),
    }).eq("telegram_id", telegram_id).execute()

    user = {**user, "queries_this_month": new_count}
    return True, user


def get_user(telegram_id: int) -> dict | None:
    sb = get_supabase_client()
    result = sb.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if not result.data:
        return None
    user = _maybe_reset(sb, result.data[0])
    return user


def get_limit_message(user: dict) -> str:
    limit = user.get("queries_limit", 10)
    reset = user.get("reset_date", "el próximo mes")
    return (
        f"⚠️ Has agotado tus {limit} análisis gratuitos este mes.\n\n"
        f"Tu acceso se renueva el {reset}.\n\n"
        f"¿Quieres análisis ilimitados? Escribe a @investx_admin para acceder al plan premium."
    )
