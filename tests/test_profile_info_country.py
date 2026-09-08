"""profile-info must surface `country` inside ahavah_extra so the web
client's completeness gate can read it.

2026-09-07: onboarding writes the person.country column but not
ahavah_extra.country, and the web client reads country only from
ahavah_extra (via the JSONB spread). So members read country=undefined
on any device without their local draft cache, wrongly failing the
discover-eligibility gate. Q_GET_PROFILE_INFO now merges the column
value into the emitted ahavah_extra when the blob lacks it.
"""
from database import api_tx
from service.person.sql import Q_GET_PROFILE_INFO


def _profile_info(tx, person_id):
    return tx.execute(Q_GET_PROFILE_INFO, dict(person_id=person_id,
                                               email='x@example.com')).fetchone()['j']


def test_country_surfaced_from_column_when_extra_lacks_it(make_person):
    p = make_person(name='Countryless', gender='Woman')
    with api_tx() as tx:
        # Column set, ahavah_extra WITHOUT a country key (the 19/24 case).
        tx.execute(
            "UPDATE person SET country = 'BB', "
            "ahavah_extra = COALESCE(ahavah_extra, '{}'::jsonb) - 'country' "
            "WHERE id = %(id)s", dict(id=p['id']))
        info = _profile_info(tx, p['id'])
        assert info['ahavah_extra'].get('country') == 'BB', \
            'country must be surfaced into ahavah_extra from the column'


def test_existing_extra_country_is_not_overwritten(make_person):
    p = make_person(name='HasExtra', gender='Man')
    with api_tx() as tx:
        # Both set but different: the blob's own value must win (no clobber).
        tx.execute(
            "UPDATE person SET country = 'US', "
            "ahavah_extra = jsonb_set(COALESCE(ahavah_extra,'{}'::jsonb),'{country}','\"DO\"') "
            "WHERE id = %(id)s", dict(id=p['id']))
        info = _profile_info(tx, p['id'])
        assert info['ahavah_extra'].get('country') == 'DO', \
            'an existing ahavah_extra.country must not be overwritten by the column'
