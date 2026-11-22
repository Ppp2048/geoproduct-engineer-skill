"""Providers available to stages, looked up by name."""

import os
from collections.abc import Iterable, Mapping

import httpx2

from geoproduct.errors import GeoProductError
from geoproduct.providers.base import Capability, Provider, ProviderInfo
from geoproduct.schemas import ErrorCode
from geoproduct.workspace import Workspace


class ProviderRegistry:
    def __init__(self, providers: Iterable[Provider] = ()) -> None:
        self._providers: dict[str, Provider] = {}
        for provider in providers:
            self.add(provider)

    def add(self, provider: Provider) -> None:
        name = provider.info.name
        if name in self._providers:
            raise ValueError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name]
        except KeyError:
            raise GeoProductError(
                ErrorCode.not_found,
                f"No provider is called '{name}'.",
                details={"provider": name, "available": self.names()},
                hint="Use one of the available providers.",
            ) from None

    def require(self, name: str, capability: Capability) -> Provider:
        provider = self.get(name)
        if not provider.supports(capability):
            raise provider.unsupported_error(capability)
        info = provider.info
        if not info.configured:
            raise GeoProductError(
                ErrorCode.auth_missing,
                f"Provider '{name}' is not configured. {info.status_note or ''}".strip(),
                details={
                    "provider": name,
                    "credentials": list(info.credentials),
                    "settings": list(info.settings),
                },
                hint="Set the environment variables it lists, then try again.",
            )
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)

    def infos(self) -> list[ProviderInfo]:
        return [self._providers[name].info for name in self.names()]


def default_providers() -> ProviderRegistry:
    """The providers that work offline, with no configuration."""
    from geoproduct.providers.fixture import FixtureProvider

    return ProviderRegistry([FixtureProvider()])


def builtin_providers(
    workspace: Workspace | None = None,
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx2.BaseTransport | None = None,
) -> ProviderRegistry:
    """The fixture plus the live OpenStreetMap providers, configured from the environment.

    Nothing is sent over the network until a stage or tool calls a live provider. Without
    ``GEOPRODUCT_USER_AGENT`` the public endpoints are listed but not usable. ``transport``
    replaces the network in tests.
    """
    from geoproduct.providers import nominatim, overpass
    from geoproduct.providers.fixture import FixtureProvider
    from geoproduct.providers.http import HttpClient, HttpSettings, Throttle

    env = os.environ if env is None else env
    settings = HttpSettings.from_env(env, workspace)
    agent = settings.user_agent is not None

    def client(name: str, throttle: Throttle) -> HttpClient:
        return HttpClient(name, settings, throttle, transport=transport)

    nominatim_url = nominatim.endpoint_from_env(env)
    return ProviderRegistry(
        [
            FixtureProvider(),
            nominatim.NominatimProvider(
                client,
                endpoint=nominatim_url,
                rate=nominatim.rate_from_env(env, nominatim.is_public(nominatim_url)),
                user_agent_set=agent,
            ),
            overpass.OverpassProvider(
                client,
                endpoint=overpass.endpoint_from_env(env),
                timeout_s=overpass.timeout_from_env(env),
                user_agent_set=agent,
            ),
        ]
    )
