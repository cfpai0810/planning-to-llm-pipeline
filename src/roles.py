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

This module imports neither the database engine nor the query parser
(tests/test_boundaries.py): the session reads the dimensions for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from src import names as allow_list
from src import outcomes, registry

__all__ = ["ENTITY_CONTROLLER", "GROUP_CONTROLLER", "Members", "Role", "RoleError",
           "entity_controller", "group_controller", "members_for"]

GROUP_CONTROLLER, ENTITY_CONTROLLER = "group_controller", "entity_controller"


class RoleError(ValueError):
    """A role that cannot be made, told in one fixed sentence."""


@dataclass(frozen=True)
class Role:
    name: str                       # group_controller or entity_controller
    entity: Optional[str] = None    # an entity controller's entity code

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


def members_for(role: Role, session) -> Union[Members, outcomes.Outcome]:
    """The member lists a prompt may use under role, from the session's promoted pair."""
    found = session.members()
    if isinstance(found, outcomes.Outcome):
        return found
    entities = tuple(e for e in found["entities"] if role.entity is None or e[0] == role.entity)
    regions = tuple(dict.fromkeys(e[2] for e in entities if e[2]))
    return Members(entities, regions, tuple(found["accounts"]), registry.load().ids())
