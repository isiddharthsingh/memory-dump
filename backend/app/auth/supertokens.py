"""SuperTokens setup: email/password sign-in, sessions, and the user dashboard.

SuperTokens serves its own routes under /auth (signup, signin, signout,
session/refresh, dashboard) through the middleware added in app.main. The app
sends `st-auth-mode: header`, so tokens travel in the `st-access-token` and
`st-refresh-token` response headers and come back as `Authorization: Bearer`.

The overrides below create the app's `users` row whenever SuperTokens creates
an email/password user, whether from the app's sign-up form or the dashboard.
"""

from __future__ import annotations

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

# Optional sign-up form field, kept in users.display_name.
NAME_FIELD = "name"
_DISPLAY_NAME_KEY = "memory_dump_display_name"
MIN_PASSWORD_LEN = 10
_MAX_FIELD_LEN = 100


async def _validate_password(value: Any, _tenant_id: str) -> str | None:
    if not isinstance(value, str) or len(value) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters"
    if len(value) > _MAX_FIELD_LEN:
        return f"Password must be at most {_MAX_FIELD_LEN} characters"
    return None


async def _validate_name(value: Any, _tenant_id: str) -> str | None:
    if not isinstance(value, str) or len(value) > _MAX_FIELD_LEN:
        return f"Name must be text of at most {_MAX_FIELD_LEN} characters"
    return None


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
        # the name through the user context.
        name = next((f.value for f in form_fields if f.id == NAME_FIELD), None)
        if isinstance(name, str) and name.strip():
            user_context[_DISPLAY_NAME_KEY] = name.strip()
        return await original_sign_up_post(
            form_fields,
            tenant_id,
            session,
            should_try_linking_with_session_user,
            api_options,
            user_context,
        )

    original.sign_up_post = sign_up_post  # type: ignore[method-assign]
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
            session.init(),
            emailpassword.init(
                sign_up_feature=emailpassword.InputSignUpFeature(
                    form_fields=[
                        emailpassword.InputFormField(id="password", validate=_validate_password),
                        emailpassword.InputFormField(
                            id=NAME_FIELD, validate=_validate_name, optional=True
                        ),
                    ]
                ),
                override=emailpassword.EmailPasswordOverrideConfig(
                    apis=lambda original: _override_apis(original, settings.allow_signup),
                    functions=_override_functions,
                ),
            ),
            # The user management UI at {api_domain}/auth/dashboard.
            dashboard.init(),
        ],
    )
