"""Custom exceptions for the Profile Registry."""

from __future__ import annotations


class RegistryError(Exception):
    """Base exception for all profile-registry errors."""


class ProfileNotFound(RegistryError):
    """Raised when a requested profile does not exist."""

    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name
        super().__init__(f"Profile not found: '{profile_name}'")


class InvalidHierarchy(RegistryError):
    """Raised when an operation would violate hierarchy rules."""


class DuplicateProfile(RegistryError):
    """Raised when attempting to create a profile that already exists."""

    def __init__(self, profile_name: str) -> None:
        self.profile_name = profile_name
        super().__init__(f"Profile already exists: '{profile_name}'")


class InvalidProfileName(RegistryError):
    """Raised when a profile name fails validation."""

    def __init__(self, profile_name: str, reason: str = "") -> None:
        self.profile_name = profile_name
        msg = f"Invalid profile name: '{profile_name}'"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


class OnboardingRequired(RegistryError):
    """Raised when an operation requires the profile to be active but it is
    still in ``onboarding`` status.

    The parent PM must complete the required onboarding artifacts before the
    profile can be activated or spawn workers.
    """

    def __init__(self, profile_name: str, missing_requirements: list[str] | None = None) -> None:
        self.profile_name = profile_name
        self.missing_requirements = missing_requirements or []
        requirement_text = ""
        if self.missing_requirements:
            requirement_text = " Missing: " + ", ".join(self.missing_requirements) + "."
        super().__init__(
            f"Profile '{profile_name}' is in onboarding status. "
            "The parent PM must complete onboarding requirements before this "
            f"profile can be activated or spawn workers.{requirement_text}"
        )


class OnboardingIncomplete(RegistryError):
    """Raised when a submitted onboarding brief is missing required fields."""

    def __init__(self, profile_name: str, missing_fields: list[str]) -> None:
        self.profile_name = profile_name
        self.missing_fields = missing_fields
        super().__init__(
            f"Onboarding brief for '{profile_name}' is missing required fields: "
            + ", ".join(missing_fields)
        )


class UnauthorizedOnboardingOwner(RegistryError):
    """Raised when someone other than the direct parent tries to onboard a profile."""

    def __init__(self, profile_name: str, owner_profile: str, expected_owner: str | None) -> None:
        self.profile_name = profile_name
        self.owner_profile = owner_profile
        self.expected_owner = expected_owner
        super().__init__(
            f"Profile '{owner_profile}' is not authorized to onboard '{profile_name}'. "
            f"Expected owner: '{expected_owner}'."
        )


class ImplementationPlanRequired(OnboardingRequired):
    """Raised when a profile has a brief but still lacks its initial plan."""

    def __init__(self, profile_name: str) -> None:
        super().__init__(profile_name, ["initial implementation plan"])
