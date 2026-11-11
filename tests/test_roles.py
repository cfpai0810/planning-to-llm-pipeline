"""Roles, member lists and the typed outcomes (part 2c, the reviewer's brief 5.4 and 5.3).

The role, not the specification, decides which entities a question may see. A group
controller sees every entity in dim_entity; an entity controller sees its own entity alone.
members_for(role, session) gives the member lists a prompt may use: entities, regions,
accounts and metrics. For an entity controller no other entity's code, name or region appears
in them (searched as text). AT and DE share a region, Central Europe: an AT controller sees
its own region's name, which is AT's own member; what must not appear is DE's code, DE's name,
or a region only another entity holds (Southern Europe, for an AT controller).

Commit 3a (the reviewer's verdict of 11 November, F21): a role of the wrong shape raises
RoleError when it is made, and wherever it is used, however it was made.
"""

import json
import re
from datetime import datetime, timezone

import pytest

NOON = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
ENTITIES = {"ES": ("Calder Foods Spain", "Southern Europe"),
            "AT": ("Calder Foods Austria", "Central Europe"),
            "DE": ("Calder Foods Germany", "Central Europe")}
METRICS = ("revenue", "cost_of_sales", "gross_profit", "operating_expenses", "ebit",
           "gross_margin_pct", "ebit_margin_pct", "marketing_pct_of_revenue", "volume",
           "headcount", "revenue_per_unit")


@pytest.fixture(scope="module")
def roles():
    from src import roles
    return roles


@pytest.fixture(scope="module")
def promoted_session(template, tmp_path_factory):
    """One session with the clean pair promoted, for every test of this module."""
    from src import session
    pool = session.Sessions(template, root=tmp_path_factory.mktemp("roles"))
    s = pool.open()
    s.promote("clean", "P. Promoter", clock=lambda: NOON)
    yield s
    pool.close_all()


def text_of(members) -> str:
    return json.dumps([list(members.entities), list(members.regions), list(members.accounts),
                       list(members.metrics)], ensure_ascii=False)


class TestTheRoles:
    def test_the_two_roles(self, roles):
        assert (roles.group_controller.name, roles.group_controller.entity) == (
            "group_controller", None)
        at = roles.entity_controller("AT")
        assert (at.name, at.entity) == ("entity_controller", "AT")
        assert roles.entity_controller("AT") == at
        assert (roles.group_controller.view(), at.view()) == ("group view", "entity view: AT")

    @pytest.mark.parametrize("entity", ["", " AT", "AT;", None, 7])
    def test_an_entity_controller_needs_an_entity_code(self, roles, entity):
        with pytest.raises(roles.RoleError):
            roles.entity_controller(entity)


class TestTheMemberLists:
    def test_with_nothing_promoted_there_are_none(self, roles, template, tmp_path):
        from src import outcomes, session
        pool = session.Sessions(template, root=tmp_path)
        try:
            assert roles.members_for(roles.group_controller, pool.open()) == \
                outcomes.NOTHING_PROMOTED
        finally:
            pool.close_all()

    def test_a_group_controller_sees_every_entity(self, roles, promoted_session):
        m = roles.members_for(roles.group_controller, promoted_session)
        assert m.entities == tuple((code, *ENTITIES[code]) for code in ("ES", "AT", "DE"))
        assert m.regions == ("Southern Europe", "Central Europe")
        assert [code for code, _ in m.accounts] == [
            "4000", "4100", "5000", "5100", "5200", "GP", "6000", "6100", "6200", "6300",
            "EBIT", "VOL", "HC"]
        assert m.metrics == METRICS

    def test_an_at_controller_sees_at_alone(self, roles, promoted_session):
        # Said plainly: an AT controller's lists hold AT (its code, its name and its region,
        # Central Europe), every account on Lists and every metric; nothing of ES or DE.
        m = roles.members_for(roles.entity_controller("AT"), promoted_session)
        assert m.entities == (("AT", "Calder Foods Austria", "Central Europe"),)
        assert m.regions == ("Central Europe",)
        assert len(m.accounts) == 13 and m.metrics == METRICS

    @pytest.mark.parametrize("entity", ["ES", "AT", "DE"])
    def test_no_other_entitys_code_name_or_region_appears(self, roles, promoted_session,
                                                          entity):
        text = text_of(roles.members_for(roles.entity_controller(entity), promoted_session))
        own_region = ENTITIES[entity][1]
        for other, (name, region) in ENTITIES.items():
            if other == entity:
                assert other in text and name in text and region in text
                continue
            assert not re.search(rf"\b{other}\b", text), other
            assert name not in text, name
            if region != own_region:
                assert region not in text, region

    def test_an_entity_the_star_does_not_hold_sees_nothing(self, roles, promoted_session):
        m = roles.members_for(roles.entity_controller("FR"), promoted_session)
        assert (m.entities, m.regions) == ((), ())


class TestTheOutcomes:
    def test_nothing_promoted_and_its_fixed_sentence(self):
        # Part 2d adds the other outcomes (tests/test_spec.py); this one keeps its sentence.
        from src import outcomes
        assert outcomes.SENTENCES["nothing_promoted"] == (
            "Nothing has been promoted yet. Review a batch first.")
        assert outcomes.NOTHING_PROMOTED == outcomes.outcome("nothing_promoted")
        with pytest.raises(KeyError):
            outcomes.outcome("something_else")

    def test_an_outcome_is_frozen(self):
        import dataclasses

        from src import outcomes
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcomes.NOTHING_PROMOTED.sentence = "changed"


# -- F21: a role of the wrong shape (the reviewer's verdict of 11 November, section 5) ------------

# The reviewer's three shapes: an entity controller with no entity, a name that is neither
# role, and a group controller with an entity.
THREE = (("entity_controller", None), ("anything", None), ("group_controller", "AT"))
WRONG = [("entity_controller",), ("anything", None), ("group_controller", "AT"),
         # Others the shape rule covers, one line each.
         ("group_controller", ""), ("entity_controller", " AT"), ("entity_controller", 7),
         ("entity_controller", "AT;"), ("Group_Controller", None), (None, None)]


def built_without_its_constructor(roles, name, entity):
    """A Role made by object.__new__, so neither its constructor nor its check runs."""
    role = object.__new__(roles.Role)
    object.__setattr__(role, "name", name)
    object.__setattr__(role, "entity", entity)
    return role


WRONG_ROLES = [f"Role{args} by object.__new__" for args in THREE] + [
    "a stand-in with a group controller's name", "a stand-in with an AT controller's name"]


def wrong_roles() -> dict:
    """Every way of reaching the code with something that is not a Role of the two shapes."""
    from types import SimpleNamespace

    from src import roles
    found = [built_without_its_constructor(roles, *args) for args in THREE] + [
        SimpleNamespace(name="group_controller", entity=None, view=lambda: "group view"),
        SimpleNamespace(name="entity_controller", entity="AT", view=lambda: "entity view: AT")]
    return dict(zip(WRONG_ROLES, found))


def question() -> dict:
    return {"metric": "revenue", "version": "Budget", "dimensions": [], "filters": [],
            "period": {"kind": "fy", "value": "FY2026"}, "comparison": "none", "sort": None,
            "limit": 10}


class TestARoleOfTheWrongShapeIsRefused:
    """F21 (verdict, section 5, item 2). A role is built by the page's own code, never from a
    visitor's words, so a wrong one is a programming fault: it raises RoleError, loudly, and is
    never answered. Role checks its shape when it is made (dataclasses.replace included): the
    name is one of the two; an entity controller has an entity code that passes the member-name
    allow-list; a group controller has none. A role made without its constructor, or an object
    that only looks like one, is caught by the same rule where it is used: validate, compile,
    session.answer and session.run, members_for and provenance."""

    @pytest.mark.parametrize("args", WRONG, ids=[repr(a) for a in WRONG])
    def test_it_cannot_be_made(self, roles, args):
        with pytest.raises(roles.RoleError):
            roles.Role(*args)

    def test_replace_runs_the_same_check(self, roles):
        import dataclasses
        for role, changes in ((roles.group_controller, {"entity": "AT"}),
                              (roles.entity_controller("AT"), {"entity": None}),
                              (roles.entity_controller("AT"), {"name": "group_controller"}),
                              (roles.entity_controller("AT"), {"name": "anything"})):
            with pytest.raises(roles.RoleError):
                dataclasses.replace(role, **changes)

    def test_the_two_shapes_are_made_as_before(self, roles):
        assert roles.Role("group_controller") == roles.group_controller
        assert roles.Role("entity_controller", "AT") == roles.entity_controller("AT")
        for role in (roles.group_controller, roles.entity_controller("AT")):
            assert roles.checked(role) is role

    @pytest.mark.parametrize("label", WRONG_ROLES)
    def test_no_door_gives_it_a_figure(self, roles, promoted_session, label):
        from src import compiler, spec
        role = wrong_roles()[label]
        s = promoted_session
        loaded = s.registry()
        asked = spec.from_json(question())
        months = tuple(f"2026-{m:02d}" for m in range(1, 13))
        entities = ("ES", "AT", "DE") if role.entity is None else (role.entity,)
        by_hand = spec.Validated(spec=asked, entities=entities, months=months)
        good = compiler.compile(spec.Validated(spec=asked, entities=("ES", "AT", "DE"),
                                               months=months), loaded, roles.group_controller)
        with pytest.raises(roles.RoleError):
            roles.checked(role)
        with pytest.raises(roles.RoleError):
            s.answer(question(), role)
        with pytest.raises(roles.RoleError):
            s.answer(asked, role)
        with pytest.raises(roles.RoleError):
            spec.validate(asked, loaded, s, role)
        with pytest.raises(roles.RoleError):
            compiler.compile(by_hand, loaded, role)
        with pytest.raises(roles.RoleError):
            s.run(good, by_hand, role)
        with pytest.raises(roles.RoleError):
            roles.members_for(role, s)
        with pytest.raises(roles.RoleError):
            s.provenance(role)
