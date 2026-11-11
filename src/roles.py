# =============================================================================
# src/roles.py: who may see which entity
# =============================================================================
"""
The two roles of the reviewer's Phase 2 brief, section 5.4. The role object,
never a specification, decides which entities a question may see; the
compiler of part 2d injects it.

- group_controller: every entity in dim_entity.
- entity_controller(entity): that one entity alone. Every total, ratio,
  ranking and "group" figure is over it, and the provenance says
  "entity view: ES".

members_for(role, session) gives the member lists a prompt may use: the
entities (code, name, region), the regions, the accounts (code, name) and the
metric ids of the registry, in their list orders. For an entity controller
the entities and regions are its own alone, so no other entity's code, name or
region appears in them. Two entities can share a region (AT and DE are both
Central Europe): an AT controller sees that region's name, which is AT's own
member. Accounts and metrics are the same for every role. With nothing
promoted there are no lists: the outcome nothing_promoted.

The shape of a role (the reviewer's verdict of 11 November, F21). A role has
one of two shapes: the group controller, with no entity; or an entity
controller, with an entity code, text that passes the member-name
allow-list. Role refuses any other shape when it is made, with RoleError;
dataclasses.replace makes a role anew, so the same check runs. A role is
built by the page's own code, never from a visitor's words, so a wrong one
is a programming fault: it stops loudly, and is never answered. A frozen
dataclass can still be made without its constructor (object.__new__), and an
object can look like a role without being one, so checked(role) applies the
same rule and also asks for a Role itself (type(role) is Role). validate,
compile, session.answer, session.run, members_for and provenance call it
before they use a role.

This module imports neither the database engine nor the query parser
(tests/test_boundaries.py): the session reads the dimensions for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from src import names as allow_list
from src import outcomes, registry

__all__ = ["ENTITY_CONTROLLER", "GROUP_CONTROLLER", "ROLES", "Members", "Role", "RoleError",
           "checked", "entity_controller", "group_controller", "members_for"]

GROUP_CONTROLLER, ENTITY_CONTROLLER = "group_controller", "entity_controller"
ROLES = (GROUP_CONTROLLER, ENTITY_CONTROLLER)
SHAPE = ("A role is the group controller, with no entity, or an entity controller, with one "
         "entity code.")


class RoleError(ValueError):
    """A role that cannot be made, or is not a role, told in one fixed sentence."""


def _wrong_shape(name, entity) -> bool:
    """True unless (name, entity) is one of the two shapes of a role."""
    if type(name) is not str or name not in ROLES:
        return True
    if name == GROUP_CONTROLLER:
        return entity is not None
    return type(entity) is not str or not allow_list.allowed(entity)


@dataclass(frozen=True)
class Role:
    name: str                       # group_controller or entity_controller
    entity: Optional[str] = None    # an entity controller's entity code

    def __post_init__(self) -> None:
        if _wrong_shape(self.name, self.entity):
            raise RoleError(SHAPE)

    def view(self) -> str:
        """What the provenance says the figures are over."""
        return "group view" if self.entity is None else f"entity view: {self.entity}"


@dataclass(frozen=True)
class Members:
    """The member lists a prompt may use, filtered by the role."""
    entities: tuple                 # (code, name, region), in Lists order
    regions: tuple                  # in the order of their first entity
    accounts: tuple                 # (code, name), in Lists order
    metrics: tuple                  # the registry's metric ids, in its order


group_controller = Role(GROUP_CONTROLLER)


def entity_controller(entity) -> Role:
    """The role that sees one entity alone: its code must pass the member-name allow-list."""
    if not isinstance(entity, str) or not allow_list.allowed(entity):
        raise RoleError("An entity controller needs an entity code.")
    return Role(ENTITY_CONTROLLER, entity)


def checked(role) -> Role:
    """The role itself when it is a Role of one of the two shapes, however it was made;
    anything else raises RoleError."""
    if type(role) is not Role or _wrong_shape(getattr(role, "name", None),
                                              getattr(role, "entity", None)):
        raise RoleError(SHAPE)
    return role


def members_for(role: Role, session) -> Union[Members, outcomes.Outcome]:
    """The member lists a prompt may use under role, from the session's promoted pair."""
    role = checked(role)
    found = session.members()
    if isinstance(found, outcomes.Outcome):
        return found
    entities = tuple(e for e in found["entities"] if role.entity is None or e[0] == role.entity)
    regions = tuple(dict.fromkeys(e[2] for e in entities if e[2]))
    return Members(entities, regions, tuple(found["accounts"]), registry.load().ids())
