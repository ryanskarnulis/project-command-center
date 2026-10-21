"""Top-level argument models for the tools that take a whole mutation payload (#411).

``create_task``, ``update_task``, ``create_project`` and ``update_project`` take a
``TaskCreate``/``TaskUpdate``/``ProjectCreate``/``ProjectUpdate`` in their bodies,
but advertise its fields as top-level arguments: a nested ``data``/``changes``
object hid them behind a ``$ref`` the 12B model kept missing (#404).

The flat model is built from the payload model's own fields, so the schema keys
pydantic emits are unchanged — only the nesting goes. It is strict
(``extra="forbid"``, as every mutation input is since #164), and it keeps the
omit-versus-null distinction: the tool body receives the payload model built
from the arguments actually sent, so ``model_dump(exclude_unset=True)`` still
tells "leave it" from "clear it".

Before validation it forgives the shapes the model has been seen to send:

- a ``data``/``changes`` wrapper, at any depth (the old nesting, and the
  double wrap from the deployed history), is lifted to the top level;
- the other item's word for the name (``name`` for a task's ``title``,
  ``title`` for a project's ``name``) and ``parent_id`` for ``parent_task_id``
  are renamed. When both are sent the canonical key wins.

The advertised schema names only the canonical keys.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

import structlog
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model, model_validator
from pydantic.fields import FieldInfo

from app.schemas import focus as focus_schemas
from app.schemas import tasks as task_schemas

logger = structlog.get_logger(__name__)

_WRAPPER_KEYS = ("data", "changes")


# The tools' cadences: ``every`` defaults to 1, where the REST schemas keep it
# required — a model saying "weekly" sends ``{"unit": "week"}`` and means every
# week (#411). Named like the REST models so the advertised ``$defs`` keep their
# names, and the docstrings are model-facing: they become schema descriptions.


class RepeatInterval(task_schemas.RepeatInterval):
    """A task recurrence cadence, e.g. ``{"unit": "week", "every": 2}``; ``every`` defaults to 1."""

    every: Annotated[int, Field(ge=1, le=12)] = 1


class CommitmentRepeatInterval(focus_schemas.CommitmentRepeatInterval):
    """A commitment series' cadence; ``every`` defaults to 1.

    ``{"unit": "week", "every": 1, "days": [0, 1, 2, 3, 4]}`` is Mon-Fri (days:
    Monday 0 to Sunday 6, weeks only).
    """

    every: Annotated[int, Field(ge=1, le=12)] = 1


class FlatArgs(ArgModelBase):
    """Base of a flat tool-argument model; see :func:`flat_args`."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    tool_name: ClassVar[str]
    payload_model: ClassVar[type[BaseModel]]
    payload_param: ClassVar[str]
    leading: ClassVar[tuple[str, ...]]
    aliases: ClassVar[dict[str, str]]

    _payload: BaseModel = PrivateAttr()

    @model_validator(mode="before")
    @classmethod
    def _forgive_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        args = dict(value)
        unwrapped = 0
        while True:
            rest = [key for key in args if key not in cls.leading]
            if len(rest) != 1 or rest[0] not in _WRAPPER_KEYS:
                break
            inner = args[rest[0]]
            if not isinstance(inner, dict):
                break
            del args[rest[0]]
            args = {**inner, **args}
            unwrapped += 1
        renamed: list[str] = []
        for alias, canonical in cls.aliases.items():
            if alias in args:
                aliased = args.pop(alias)
                args.setdefault(canonical, aliased)
                renamed.append(alias)
        if unwrapped or renamed:
            logger.info(
                "tool_args_forgiven", tool=cls.tool_name, unwrapped=unwrapped, renamed=renamed
            )
        return args

    @model_validator(mode="after")
    def _build_payload(self) -> FlatArgs:
        # Re-validate into the payload model so its own validators (the
        # non-nullable checks) run, with only the fields actually sent: the
        # payload's ``model_fields_set`` is the caller's.
        sent = self.model_fields_set - set(self.leading)
        self._payload = self.payload_model.model_validate(self.model_dump(include=sent))
        return self

    def model_dump_one_level(self) -> dict[str, Any]:
        kwargs = {name: getattr(self, name) for name in self.leading}
        kwargs[self.payload_param] = self._payload
        return kwargs


def flat_args(
    tool_name: str,
    payload_model: type[BaseModel],
    *,
    payload_param: str,
    leading: dict[str, tuple[Any, Any]] | None = None,
    aliases: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> type[FlatArgs]:
    """Build ``<tool_name>Arguments``: ``leading`` fields first, then the payload's.

    ``overrides`` swaps a payload field's annotation (keeping its default) for
    one only the tool accepts, e.g. a ``repeat_interval`` whose ``every``
    defaults to 1.
    """
    leading = leading or {}
    overrides = overrides or {}
    fields: dict[str, Any] = dict(leading)
    for name, info in payload_model.model_fields.items():
        annotation = overrides.get(name, info.annotation)
        fields[name] = (annotation, FieldInfo.merge_field_infos(info, annotation=annotation))
    model = create_model(
        f"{tool_name}Arguments", __base__=FlatArgs, **fields
    )
    model.tool_name = tool_name
    model.payload_model = payload_model
    model.payload_param = payload_param
    model.leading = tuple(leading)
    model.aliases = aliases or {}
    return model
