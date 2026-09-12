"""Public contracts for verified account contacts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PasswordLoginRequest(StrictRequest):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class PhoneRequest(StrictRequest):
    phone: str = Field(min_length=9, max_length=40)

    @field_validator("phone")
    @classmethod
    def phone_number(cls, value: str) -> str:
        value = re.sub(r"[\s()\-]", "", value)
        if not re.fullmatch(r"\+992[0-9]{9}", value):
            raise ValueError("Use +992 followed by nine digits")
        return value


class PhoneVerifyRequest(PhoneRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")


class PhoneBindingRequest(PhoneRequest):
    password: str | None = Field(default=None, max_length=1024)
    reauth_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    mfa_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class ContactCodeRequest(StrictRequest):
    channel: Literal["email", "phone"]
    contact: str = Field(min_length=1, max_length=320)

    @model_validator(mode="after")
    def normalize(self) -> ContactCodeRequest:
        if self.channel == "phone":
            self.contact = PhoneRequest(phone=self.contact).phone
        else:
            # Reuse Pydantic's email validator rather than inventing a mailbox parser.
            self.contact = str(EmailContact(email=self.contact).email).lower()
        return self


class EmailContact(BaseModel):
    email: EmailStr


class PasswordResetRequest(ContactCodeRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")
    new_password: str = Field(min_length=12, max_length=128)
    mfa_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    recovery_code: str | None = Field(default=None, min_length=20, max_length=32)

    @field_validator("new_password")
    @classmethod
    def _bcrypt_input_limit(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Password cannot contain a NUL character")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must fit within 72 UTF-8 bytes")
        return value


class SetupChannelRequest(StrictRequest):
    channel: Literal["email", "phone"] = "email"


class IdentityResponse(BaseModel):
    phone: str | None
    phone_verified: bool
    has_password: bool
    password_prompt_pending: bool


class LoginMethodsResponse(BaseModel):
    phone_login_enabled: bool
