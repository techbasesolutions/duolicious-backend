"""Billing read surfaces (2026-05-20) — get_subscription / get_invoices /
get_billing_portal flow deep-link.

Stripe is mocked via monkeypatch (no live Stripe, no DB): we patch
service.checkout._stripe and service.checkout._customer_id_for.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import service.checkout as checkout


def _session(person_id=1):
    return SimpleNamespace(person_id=person_id, person_uuid='u-1')


@pytest.fixture
def stripe_mock(monkeypatch):
    m = MagicMock(name='stripe')
    monkeypatch.setattr(checkout, '_stripe', lambda: m)
    monkeypatch.setattr(checkout, '_customer_id_for', lambda pid: 'cus_123')
    return m


def test_subscription_maps_fields(stripe_mock):
    stripe_mock.Subscription.list.return_value = {
        'data': [{
            'status': 'active',
            'current_period_end': 1750000000,
            'cancel_at_period_end': False,
            'items': {'data': [{'price': {
                'unit_amount': 899,
                'recurring': {'interval': 'month'},
            }}]},
            'default_payment_method': {'card': {'brand': 'visa', 'last4': '4242'}},
        }],
    }
    out = checkout.get_subscription(_session())
    assert out['status'] == 'active'
    assert out['plan_label'] == 'Premium'
    assert out['price_label'] == '$8.99 / month'
    assert out['current_period_end'] == 1750000000
    assert out['cancel_at_period_end'] is False
    assert out['card_brand'] == 'visa'
    assert out['card_last4'] == '4242'


def test_subscription_none_when_no_customer(monkeypatch):
    monkeypatch.setattr(checkout, '_stripe', lambda: MagicMock())
    monkeypatch.setattr(checkout, '_customer_id_for', lambda pid: None)
    assert checkout.get_subscription(_session()) == {'status': 'none'}


def test_subscription_none_when_no_subs(stripe_mock):
    stripe_mock.Subscription.list.return_value = {'data': []}
    assert checkout.get_subscription(_session()) == {'status': 'none'}


def test_subscription_401_without_session():
    out = checkout.get_subscription(None)
    assert out[1] == 401


def test_invoices_map_links(stripe_mock):
    stripe_mock.Invoice.list.return_value = {
        'data': [
            {'id': 'in_1', 'created': 1740000000, 'amount_paid': 899,
             'currency': 'usd', 'status': 'paid',
             'hosted_invoice_url': 'https://stripe/h/1',
             'invoice_pdf': 'https://stripe/p/1.pdf'},
        ],
    }
    out = checkout.get_invoices(_session())
    inv = out['invoices'][0]
    assert inv['id'] == 'in_1'
    assert inv['amount_label'] == '$8.99 USD'
    assert inv['status'] == 'paid'
    assert inv['hosted_invoice_url'] == 'https://stripe/h/1'
    assert inv['invoice_pdf'] == 'https://stripe/p/1.pdf'


def test_invoices_empty_when_no_customer(monkeypatch):
    monkeypatch.setattr(checkout, '_stripe', lambda: MagicMock())
    monkeypatch.setattr(checkout, '_customer_id_for', lambda pid: None)
    assert checkout.get_invoices(_session()) == {'invoices': []}


def test_portal_flow_passthrough(stripe_mock):
    stripe_mock.billing_portal.Session.create.return_value = SimpleNamespace(
        url='https://stripe/portal'
    )
    out = checkout.get_billing_portal(_session(), flow='subscription_cancel')
    assert out == {'url': 'https://stripe/portal'}
    _, kwargs = stripe_mock.billing_portal.Session.create.call_args
    assert kwargs['flow_data'] == {'type': 'subscription_cancel'}


def test_portal_ignores_unknown_flow(stripe_mock):
    stripe_mock.billing_portal.Session.create.return_value = SimpleNamespace(
        url='https://stripe/portal'
    )
    checkout.get_billing_portal(_session(), flow='hack_me')
    _, kwargs = stripe_mock.billing_portal.Session.create.call_args
    assert 'flow_data' not in kwargs
