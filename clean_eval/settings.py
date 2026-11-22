"""clean-eval's settings: every value a run takes from its environment, in groups of what belongs together (the run's
folders, a review's inputs, an eval's, the heat map's, the agent's, what GitHub sets, Claude's credentials, keyless
federation). The GitHub Action passes its inputs as CE_* variables beside what the runner sets, and the command line
reads them once into `Settings`, then hands that on: the use cases and the Claude Code gateway take it as it is, and no
other code reads a variable by name. Every layer but the domain may import this module; only the drivers
(external/cli.py) build a `Settings`.

Each setting keeps the variable it is read from (`settings.review.pr` is CE_PR): the groups shape the settings, not the
variables. Settings come from environment variables only, never from a .env file or a secrets folder: in CI the
working directory is the checkout under review, which a PR controls.

Not settings, on purpose:
- a repository's own tuning (.clean-eval/risk-model.json and review-rules.md), which CI reads from the PR's base commit
  so that a PR can't relax the rules it is judged by;
- the variables that redaction scans (every one: a secret's name isn't known in advance) and the ones the agent gets
  (patterns, since Bedrock's and Vertex's are open-ended);
- the guard's two roots, which the Claude Code gateway sets for its own hook."""

import datetime as dt
from enum import StrEnum
from typing import (
    Optional,
)

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

CLAUDE_CODE_VERSION = "2.1.328"  # the Claude Code the action installs (action.yml's claude-code-version default)
MAX_TURNS = 60
LOOKBACK_DAYS = 90  # how far back an eval reads when no first day is given
DIAGRAM_PATH = "docs/risk-heatmap.svg"  # where the README's heat-map diagram goes, relative to the repository


class FailOn(StrEnum):
    """What fails a PR review's run: nothing, a request_changes verdict, or a blocker finding."""

    NEVER = "never"
    REQUEST_CHANGES = "request_changes"
    BLOCKER = "blocker"


# A group of settings: read-only, each field named by the variable it is read from.
GROUP = ConfigDict(
    frozen=True,
)


class RunSettings(BaseModel):
    """Where a run works: the checkout it runs on, and the folder its outputs go to."""

    model_config = GROUP

    workspace: str = Field(
        ".",
        validation_alias=AliasChoices(
            "CE_WORKSPACE",
            "GITHUB_WORKSPACE",
        ),
        description="the checkout the skill runs on (default: $GITHUB_WORKSPACE, else the current folder)",
    )
    output_dir: Optional[str] = Field(
        None,
        alias="CE_OUTPUT_DIR",
        description="where report.md, result.json and claude.log go (default: $RUNNER_TEMP/clean-eval)",
    )
    runner_temp: str = Field(
        "/tmp",
        alias="RUNNER_TEMP",
        description="the runner's temporary folder",
    )

    @property
    def out(
        self,
    ) -> str:
        """The run's output folder: output_dir, or clean-eval/ in the runner's temporary folder."""
        return self.output_dir or self.runner_temp.rstrip("/") + "/clean-eval"


class ReviewSettings(BaseModel):
    """A PR review's inputs: the PR, whether to post the review, and what fails the run."""

    model_config = GROUP

    pr: Optional[int] = Field(
        None,
        alias="CE_PR",
        description="pr-review: the PR number",
    )
    post: bool = Field(
        True,
        alias="CE_POST",
        description="pr-review: post the review",
    )
    fail_on: FailOn = Field(
        FailOn.NEVER,
        alias="CE_FAIL_ON",
        description="pr-review: fail the run on a request_changes verdict, or on a blocker finding",
    )


class EvaluationSettings(BaseModel):
    """An eval's inputs: its period, and whether the agent may edit the repository's tuning."""

    model_config = GROUP

    since: Optional[dt.date] = Field(
        None,
        alias="CE_SINCE",
        description="eval: the first day (default: %d days ago)" % LOOKBACK_DAYS,
    )
    until: Optional[dt.date] = Field(
        None,
        alias="CE_UNTIL",
        description="eval: the last day (default: today)",
    )
    apply: bool = Field(
        False,
        alias="CE_APPLY",
        description="eval: let the agent edit .clean-eval/ (an edit is kept if it validates)",
    )


class HeatmapSettings(BaseModel):
    """The risk heat map's inputs: where its README and diagram go, and whether the agent levels the aspects."""

    model_config = GROUP

    readme: str = Field(
        "README.md",
        alias="CE_README",
        description="risk-heat-map: the README, in the workspace",
    )
    diagram: str = Field(
        DIAGRAM_PATH,
        alias="CE_DIAGRAM",
        description="risk-heat-map: the diagram, in the workspace",
    )
    use_claude: bool = Field(
        False,
        alias="CE_USE_CLAUDE",
        description="risk-heat-map: let the agent level the four aspects",
    )


class AgentSettings(BaseModel):
    """The CI agent: its model and turn limit, the Claude Code to install, and what to add to its prompt."""

    model_config = GROUP

    model: Optional[str] = Field(
        None,
        alias="CE_MODEL",
        description="the agent's model (default: Claude Code's)",
    )
    max_turns: int = Field(
        MAX_TURNS,
        ge=1,
        alias="CE_MAX_TURNS",
        description="the agent's turn limit",
    )
    claude_code_version: str = Field(
        CLAUDE_CODE_VERSION,
        alias="CE_CLAUDE_VERSION",
        description="the Claude Code to install when claude is missing",
    )
    extra_prompt: str = Field(
        "",
        alias="CE_EXTRA_PROMPT",
        description="added to the agent's prompt",
    )


class GitHubSettings(BaseModel):
    """What GitHub Actions sets: the step's outputs and job-summary files, that this is its runner, and gh's token."""

    model_config = GROUP

    step_outputs: str = Field(
        "/dev/null",
        alias="GITHUB_OUTPUT",
        description="the step-outputs file",
    )
    step_summary: str = Field(
        "/dev/null",
        alias="GITHUB_STEP_SUMMARY",
        description="the job-summary file",
    )
    in_actions: bool = Field(
        False,
        alias="GITHUB_ACTIONS",
        description="set on a GitHub Actions runner",
    )
    token: Optional[SecretStr] = Field(
        None,
        alias="GH_TOKEN",
        description="the token gh reads",
    )


class CredentialSettings(BaseModel):
    """Claude's ways to sign in besides federation: an API key or token, an OAuth token, Bedrock or Vertex. Claude Code
    reads them; clean-eval checks that one way is set, and only one."""

    model_config = GROUP

    api_key: Optional[SecretStr] = Field(
        None,
        alias="ANTHROPIC_API_KEY",
        description="an Anthropic API key",
    )
    auth_token: Optional[SecretStr] = Field(
        None,
        alias="ANTHROPIC_AUTH_TOKEN",
        description="a bearer token for the API (in place of a key)",
    )
    oauth_token: Optional[SecretStr] = Field(
        None,
        alias="CLAUDE_CODE_OAUTH_TOKEN",
        description="a Claude Code OAuth token",
    )
    bedrock: bool = Field(
        False,
        alias="CLAUDE_CODE_USE_BEDROCK",
        description="Claude on Amazon Bedrock",
    )
    vertex: bool = Field(
        False,
        alias="CLAUDE_CODE_USE_VERTEX",
        description="Claude on Google Vertex AI",
    )

    @property
    def sign_ins(
        self,
    ) -> list[str]:
        """The variables of the ways to sign in that are set."""
        return [str(field.alias) for name, field in type(self).model_fields.items() if getattr(self, name)]


class FederationSettings(BaseModel):
    """Keyless sign-in, by workload identity federation: the rule the job's OIDC token is traded under, the
    organization, the service account and workspace, and the job's means to request its token (`id-token: write`)."""

    model_config = GROUP

    rule_id: Optional[str] = Field(
        None,
        alias="ANTHROPIC_FEDERATION_RULE_ID",
        description="keyless: the workload identity federation rule the job's OIDC token is traded under",
    )
    organization_id: Optional[str] = Field(
        None,
        alias="ANTHROPIC_ORGANIZATION_ID",
        description="keyless: the Anthropic organization",
    )
    service_account_id: Optional[str] = Field(
        None,
        alias="ANTHROPIC_SERVICE_ACCOUNT_ID",
        description="keyless: the service account to act as",
    )
    workspace_id: Optional[str] = Field(
        None,
        alias="ANTHROPIC_WORKSPACE_ID",
        description="keyless: the workspace, when the rule covers several",
    )
    token_request_url: Optional[str] = Field(
        None,
        alias="ACTIONS_ID_TOKEN_REQUEST_URL",
        description="with `id-token: write`: where the job requests its OIDC token",
    )
    token_request_token: Optional[SecretStr] = Field(
        None,
        alias="ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        description="with `id-token: write`: the request's bearer token",
    )


class GroupedEnvironment(EnvSettingsSource):
    """The environment as Settings reads it: {group: {variable: value}}, each group given the variables its settings
    name (names matched exactly, empty variables left out), so that one validation reports every setting that doesn't
    fit."""

    def __call__(
        self,
    ) -> dict[str, dict[str, str]]:
        return {
            group: {name: value for name, value in self.env_vars.items() if name in names and value is not None}
            for group, names in Settings.group_variables().items()
        }


class Settings(BaseSettings):
    """A run's settings, in groups. Each setting is read from the environment variable it names (or the first of them
    that is set); an empty variable counts as unset, as action.yml passes an input nobody gave, and every value that
    doesn't fit is refused, together."""

    model_config = SettingsConfigDict(
        frozen=True,
        extra="ignore",
        case_sensitive=True,
        env_ignore_empty=True,
    )

    run: RunSettings = RunSettings()
    review: ReviewSettings = ReviewSettings()
    evaluation: EvaluationSettings = EvaluationSettings()
    heatmap: HeatmapSettings = HeatmapSettings()
    agent: AgentSettings = AgentSettings()
    github: GitHubSettings = GitHubSettings()
    credentials: CredentialSettings = CredentialSettings()
    federation: FederationSettings = FederationSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """The environment, grouped, and nothing from files: no .env file or secrets folder, which in CI the checkout
        under review would provide."""
        return (
            init_settings,
            GroupedEnvironment(
                settings_cls,
            ),
        )

    @classmethod
    def fields(
        cls,
    ) -> dict[str, FieldInfo]:
        """Every setting, by its dotted name (`review.pr`), group by group."""
        found = {}
        for group, info in cls.model_fields.items():
            model = info.annotation
            if isinstance(model, type) and issubclass(model, BaseModel):
                found.update({"%s.%s" % (group, name): field for name, field in model.model_fields.items()})
        return found

    @classmethod
    def variables(
        cls,
        setting: str,
    ) -> list[str]:
        """The environment variables the setting `setting` (a dotted name) is read from, in order: the first one set
        wins."""
        field = cls.fields()[setting]
        if isinstance(field.validation_alias, AliasChoices):
            return [str(choice) for choice in field.validation_alias.choices]
        return [str(field.alias)]

    @classmethod
    def group_variables(
        cls,
    ) -> dict[str, set[str]]:
        """The variables each group's settings are read from, by group."""
        found: dict[str, set[str]] = {group: set() for group in cls.model_fields}
        for setting in cls.fields():
            found[setting.split(".")[0]].update(cls.variables(setting))
        return found
