"""Roles, member lists and the typed outcomes (part 2c, the reviewer's brief 5.4 and 5.3).

The role, not the specification, decides which entities a question may see. A group
controller sees every entity in dim_entity; an entity controller sees its own entity alone.
members_for(role, session) gives the member lists a prompt may use: entities, regions,
accounts and metrics. For an entity controller no other entity's code, name or region appears
in them (searched as text). AT and DE share a region, Central Europe: an AT controller sees
its own region's name, which is AT's own member; what must not appear is DE's code, DE's name,
or a region only another entity holds (Southern Europe, for an AT controller).
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
        from src import outcomes
        assert outcomes.SENTENCES == {
            "nothing_promoted": "Nothing has been promoted yet. Review a batch first."}
        assert outcomes.NOTHING_PROMOTED == outcomes.outcome("nothing_promoted")
        with pytest.raises(KeyError):
            outcomes.outcome("something_else")

    def test_an_outcome_is_frozen(self):
        import dataclasses

        from src import outcomes
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcomes.NOTHING_PROMOTED.sentence = "changed"
