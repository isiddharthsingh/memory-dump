"""SuperTokens setup: email/password sign-in, sessions, and the user dashboard.

SuperTokens serves its own routes under /auth (signup, signin, signout,
session/refresh, dashboard) through the middleware added in app.main. The app
sends `st-auth-mode: header`, so tokens travel in the `st-access-token` and
`st-refresh-token` response headers and come back as `Authorization: Bearer`.

The overrides below create the app's `users` row whenever SuperTokens creates
an email/password user, whether from the app's sign-up form or the dashboard.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from supertokens_python import InputAppInfo, SupertokensConfig, init
from supertokens_python.asyncio import delete_user
from supertokens_python.recipe import dashboard, emailpassword, session
from supertokens_python.recipe.emailpassword.interfaces import (
    APIInterface,
    APIOptions,
    RecipeInterface,
    SignUpOkResult,
)
from supertokens_python.recipe.emailpassword.types import FormField
from supertokens_python.recipe.session.interfaces import SessionContainer
from supertokens_python.types.response import GeneralErrorResponse

from app.config import Settings
from app.db.models import User
from app.db.session import get_sessionmaker

# Optional sign-up form fields, kept in users.display_name and users.phone. The
# app asks for both; builds from before the phone field and the dashboard send
# neither.
NAME_FIELD = "name"
PHONE_FIELD = "phone"
_DISPLAY_NAME_KEY = "memory_dump_display_name"
_PHONE_KEY = "memory_dump_phone"
MIN_PASSWORD_LEN = 10
MAX_FIELD_LEN = 100


async def _validate_password(value: Any, _tenant_id: str) -> str | None:
    if not isinstance(value, str) or len(value) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters"
    if len(value) > MAX_FIELD_LEN:
        return f"Password must be at most {MAX_FIELD_LEN} characters"
    return None


async def _validate_name(value: Any, _tenant_id: str) -> str | None:
    if not isinstance(value, str) or len(value) > MAX_FIELD_LEN:
        return f"Name must be text of at most {MAX_FIELD_LEN} characters"
    return None


def phone_number(value: Any) -> str | None:
    """A phone number as `+` and its digits, country code first, or None when
    it isn't one: a `+`, then 7 to 15 digits 0-9 of which the first isn't 0
    (no country code starts with it), spaced or punctuated the usual ways."""
    if not isinstance(value, str):
        return None
    number = re.sub(r"[\s().-]", "", value)
    return number if re.fullmatch(r"\+[1-9][0-9]{6,14}", number) else None


BAD_PHONE = "Enter a valid phone number with its country code"


async def _validate_phone(value: Any, _tenant_id: str) -> str | None:
    return None if phone_number(value) else BAD_PHONE


def _override_apis(original: APIInterface, allow_signup: bool) -> APIInterface:
    original_sign_up_post = original.sign_up_post

    async def sign_up_post(
        form_fields: list[FormField],
        tenant_id: str,
        session: SessionContainer | None,
        should_try_linking_with_session_user: bool | None,
        api_options: APIOptions,
        user_context: dict[str, Any],
    ):
        if not allow_signup:
            # Sign-in still works, and the dashboard can still create users.
            return GeneralErrorResponse("Sign-ups are closed on this server.")
        # The recipe's sign_up below only sees email and password, so hand it
        # the name and phone through the user context.
        name = next((f.value for f in form_fields if f.id == NAME_FIELD), None)
        if isinstance(name, str) and name.strip():
            user_context[_DISPLAY_NAME_KEY] = name.strip()
        phone = next((f.value for f in form_fields if f.id == PHONE_FIELD), None)
        user_context[_PHONE_KEY] = phone_number(phone)
        return await original_sign_up_post(
            form_fields,
            tenant_id,
            session,
            should_try_linking_with_session_user,
            api_options,
            user_context,
        )

    original.sign_up_post = sign_up_post  # type: ignore[method-assign]
    # No password reset until there's an email provider of our own: the SDK's
    # would send each address to SuperTokens' mail service with a link to a
    # page nothing serves, to anyone who asks, unlimited. And no "does this
    # email have an account" lookup, which the app doesn't use and anyone
    # could ask of every address.
    original.disable_generate_password_reset_token_post = True
    original.disable_password_reset_post = True
    original.disable_email_exists_get = True
    return original


def _override_functions(original: RecipeInterface) -> RecipeInterface:
    original_sign_up = original.sign_up

    async def sign_up(
        email: str,
        password: str,
        tenant_id: str,
        session: SessionContainer | None,
        should_try_linking_with_session_user: bool | None,
        user_context: dict[str, Any],
    ):
        result = await original_sign_up(
            email, password, tenant_id, session, should_try_linking_with_session_user, user_context
        )
        if isinstance(result, SignUpOkResult) and len(result.user.login_methods) == 1:
            try:
                async with get_sessionmaker()() as db:
                    db.add(
                        User(
                            id=uuid.UUID(result.user.id),
                            display_name=user_context.get(_DISPLAY_NAME_KEY),
                            phone=user_context.get(_PHONE_KEY),
                        )
                    )
                    await db.commit()
            except Exception:
                # No session has been issued yet; drop the login so the email
                # can sign up again instead of owning an account with no row.
                await delete_user(result.user.id)
                raise
        return result

    original.sign_up = sign_up  # type: ignore[method-assign]
    return original


def init_supertokens(settings: Settings) -> None:
    init(
        app_info=InputAppInfo(
            app_name="Memory Dump",
            api_domain=settings.api_domain,
            # There is no website; SuperTokens still needs one for links it builds.
            website_domain=settings.api_domain,
            api_base_path="/auth",
        ),
        supertokens_config=SupertokensConfig(
            connection_uri=settings.supertokens_connection_uri,
            api_key=settings.supertokens_api_key or None,
        ),
        framework="fastapi",
        mode="asgi",
        telemetry=False,
        recipe_list=[
            # Header-based only, as the app uses them: no cookie sessions,
            # which would need CSRF protection this API doesn't have.
            session.init(get_token_transfer_method=lambda _request, _for_create, _ctx: "header"),
            emailpassword.init(
                sign_up_feature=emailpassword.InputSignUpFeature(
                    form_fields=[
                        emailpassword.InputFormField(id="password", validate=_validate_password),
                        emailpassword.InputFormField(
                            id=NAME_FIELD, validate=_validate_name, optional=True
                        ),
                        emailpassword.InputFormField(
                            id=PHONE_FIELD, validate=_validate_phone, optional=True
                        ),
                    ]
                ),
                override=emailpassword.EmailPasswordOverrideConfig(
                    apis=lambda original: _override_apis(original, settings.allow_signup),
                    functions=_override_functions,
                ),
            ),
            # The user management UI at {api_domain}/auth/dashboard, only
            # while DASHBOARD_ENABLED is set (see Settings.dashboard_enabled).
            *([dashboard.init()] if settings.dashboard_enabled else []),
        ],
    )
