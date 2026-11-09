"""Application settings, loaded from the environment (and `.env` in development).

Import `get_settings()` rather than constructing `Settings` directly — it's cached, so the
environment is read and validated once per process.

Secrets are `SecretStr`, so printing or logging a `Settings` instance masks them. Call
`.get_secret_value()` at the point of use.
"""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ASYNC_DB_SCHEME = "postgresql+asyncpg://"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Relative to the working directory, so run from `backend/`.
        env_file=".env",
        env_file_encoding="utf-8",
        # Reject unknown keys in .env, so a typo'd variable name fails loudly at startup
        # instead of silently leaving a setting at its default.
        extra="forbid",
    )

    environment: Literal["development", "staging", "production"] = "development"

    # Embeds the database password, hence SecretStr.
    database_url: SecretStr

    # Signs the session cookie. Optional in development only — see the validator below.
    session_secret: SecretStr | None = None

    # Every origin the browser app may be served from, comma-separated. The Vite dev server
    # by default; add a tunnel or a staging domain here and CORS, the OAuth redirect and the
    # post-sign-in redirect all follow, because `app/origins.py` picks the entry matching the
    # incoming request. A list rather than one value because a deployment elsewhere otherwise
    # needs the config changed, which is how a colleague's tunnel ends up redirecting to
    # localhost.
    #
    # **Every entry must also be registered in the Google console** as an authorized redirect
    # URI (with `/api/auth/google/callback` appended). Google validates against its own list.
    #
    # The first entry is canonical: it's what links in emails use, since a background task has
    # no request to learn from.
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Integrations. Left unset, the app still starts and the corresponding feature is
    # unavailable; call sites check before using them.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    # The Stripe Price that a report costs. The id, not the amount, because Stripe is what
    # actually charges: the amount here is only what the API *displays* before checkout.
    # See `app/stripe_client.py` for why the two have to be kept in step.
    stripe_price_id: str | None = None
    # What the report costs, in cents, for display. Must match the Stripe Price's amount —
    # a price change is a change to both, and a deploy of each.
    report_price_cents: int = 2999
    report_price_currency: str = "usd"
    resend_api_key: SecretStr | None = None
    # Resend's shared sender works without verifying a domain, but only delivers to the
    # address that owns the account. Anything else is accepted by the API and goes nowhere.
    resend_from_email: str = "FutureQuest <onboarding@resend.dev>"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> list[str]:
        # `NoDecode` on the field stops pydantic-settings trying to JSON-parse the variable,
        # so what arrives from the environment is the raw comma-separated string.
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list):
            raise ValueError("ALLOWED_ORIGINS must be a comma-separated list of origins")

        # Normalised here rather than at every comparison: a trailing slash or a capitalised
        # host in .env would otherwise just silently never match.
        origins = [str(origin).strip().rstrip("/").lower() for origin in value]
        origins = [origin for origin in origins if origin]

        if not origins:
            raise ValueError("ALLOWED_ORIGINS must contain at least one origin")
        for origin in origins:
            if not origin.startswith(("http://", "https://")) or origin.count("/") != 2:
                raise ValueError(
                    f"{origin!r} is not an origin — expected scheme://host[:port] with no path"
                )
        return origins

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: SecretStr) -> SecretStr:
        # A sync URL (postgresql://) fails deep inside SQLAlchemy's async engine with a
        # confusing error. Catch it here, where the message can say what's wrong.
        if not value.get_secret_value().startswith(ASYNC_DB_SCHEME):
            raise ValueError(f"DATABASE_URL must start with {ASYNC_DB_SCHEME!r}")
        return value

    @model_validator(mode="after")
    def _require_session_secret(self) -> Self:
        # Required everywhere now, not just outside development: ownership cookies are signed
        # with it, and without one every request would look like a stranger's.
        #
        # Deliberately no default — a generated-per-boot secret would silently log everyone
        # out on restart, and a hardcoded one is worse than either.
        if not self.session_secret:
            raise ValueError(
                "SESSION_SECRET is required. Generate one with: "
                'python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def stripe_configured(self) -> bool:
        """Whether a checkout can be created.

        Both the key and the Price are needed: the key without a Price can't charge anything,
        and a Price without a key can't be looked up. A half-configured deployment should
        fail loudly at the checkout route, not quietly sell the wrong thing.
        """
        return bool(self.stripe_secret_key and self.stripe_price_id)

    @property
    def stripe_webhooks_configured(self) -> bool:
        """Whether webhook deliveries can be verified.

        Separate from `stripe_configured` because the two are deployed independently: a
        deployment can take payments before its webhook endpoint is registered, and the
        failure mode of a missing secret is a 503, not a silent refusal to grant access.
        """
        return bool(self.stripe_webhook_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment
