"""Sign-in through Cognito (moto's fake Cognito), sessions, expiry and 'finish saving'."""
import os

import boto3
import pytest

from helpers import Browser, body, setup_org
from skillops.core import audit, auth, org, util

PASSWORD = "Local-test-Pass1"


@pytest.fixture
def cognito(aws, monkeypatch):
    c = boto3.client("cognito-idp", region_name="ap-south-1")
    pool = c.create_user_pool(PoolName="staff", UsernameAttributes=["email"])["UserPool"]["Id"]
    client = c.create_user_pool_client(UserPoolId=pool, ClientName="web",
                                       ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"])
    for g in ("superadmin", "trainer"):
        c.create_group(UserPoolId=pool, GroupName=g)
    monkeypatch.setenv("USER_POOL_ID", pool)
    monkeypatch.setenv("USER_POOL_CLIENT_ID", client["UserPoolClient"]["ClientId"])
    monkeypatch.setenv("SKILLOPS_LOCAL", "0")
    return c, pool


def add_user(c, pool, email, group, temp=False):
    try:
        c.admin_create_user(UserPoolId=pool, Username=email, MessageAction="SUPPRESS",
                            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "name", "Value": "Test Person"}])
    except c.exceptions.UsernameExistsException:
        pass          # setup_org() already made the trainer through SkillOps (and Cognito)
    c.admin_set_user_password(UserPoolId=pool, Username=email, Password=PASSWORD, Permanent=not temp)
    c.admin_add_user_to_group(UserPoolId=pool, Username=email, GroupName=group)


def test_superadmin_signs_in_with_password(cognito):
    c, pool = cognito
    add_user(c, pool, "boss@robomy.test", "superadmin")
    b = Browser()
    r = b.post("/signin", {"email": "boss@robomy.test", "password": PASSWORD, "next": "/admin/trainers"}, csrf=False)
    assert r["statusCode"] == 303 and r["headers"]["location"] == "/admin/trainers"
    assert "so_sid" in b.cookies
    cookie = [x for x in r["cookies"] if x.startswith("so_sid=")][0]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=Lax" in cookie
    assert b.get("/admin/trainers")["statusCode"] == 200
    assert audit.read(action="signin")[0]["result"] == "ok"


def test_wrong_password_is_plain_and_audited(cognito):
    c, pool = cognito
    add_user(c, pool, "boss@robomy.test", "superadmin")
    r = Browser().post("/signin", {"email": "boss@robomy.test", "password": "nope"}, csrf=False)
    assert r["statusCode"] == 401
    assert "The email or password is not correct." in body(r)
    assert audit.read(action="signin")[0]["result"] == "failed"


def test_first_sign_in_asks_for_a_new_password(cognito):
    c, pool = cognito
    setup_org()
    add_user(c, pool, "trainer1@robomy.test", "trainer", temp=True)
    b = Browser()
    r = b.post("/signin", {"email": "trainer1@robomy.test", "password": PASSWORD}, csrf=False)
    assert r["statusCode"] == 303 and r["headers"]["location"].startswith("/signin/new-password?c=")
    page = b.get(r["headers"]["location"])
    assert "Choose your password" in body(page)
    cid = r["headers"]["location"].split("c=")[1]
    r = b.post("/signin/new-password", {"c": cid, "password": "Brand-New-Pass9", "password2": "Brand-New-Pass9"},
               csrf=False)
    assert r["statusCode"] == 303, body(r)
    assert b.get("/")["statusCode"] == 303       # one batch: straight to it
    assert b.get(b.get("/")["headers"]["location"])["statusCode"] == 200


def test_trainer_account_turned_off_cannot_sign_in(cognito):
    c, pool = cognito
    setup_org()
    add_user(c, pool, "trainer1@robomy.test", "trainer")
    org.store.update("CORE", "TRAINER#trainer1@robomy.test", "META", {"enabled": False})
    r = Browser().post("/signin", {"email": "trainer1@robomy.test", "password": PASSWORD}, csrf=False)
    assert r["statusCode"] == 403 and "turned off" in body(r)


def test_cognito_user_without_a_group_is_refused(cognito):
    c, pool = cognito
    c.admin_create_user(UserPoolId=pool, Username="x@robomy.test", MessageAction="SUPPRESS",
                        UserAttributes=[{"Name": "email", "Value": "x@robomy.test"}])
    c.admin_set_user_password(UserPoolId=pool, Username="x@robomy.test", Password=PASSWORD, Permanent=True)
    r = Browser().post("/signin", {"email": "x@robomy.test", "password": PASSWORD}, csrf=False)
    assert r["statusCode"] == 403 and "not a SkillOps staff account" in body(r)


def test_next_cannot_send_people_to_another_site(cognito):
    c, pool = cognito
    add_user(c, pool, "boss@robomy.test", "superadmin")
    r = Browser().post("/signin", {"email": "boss@robomy.test", "password": PASSWORD,
                                   "next": "https://evil.example/x"}, csrf=False)
    assert r["headers"]["location"] == "/"
    r = Browser().post("/signin", {"email": "boss@robomy.test", "password": PASSWORD, "next": "//evil.example"},
                       csrf=False)
    assert r["headers"]["location"] == "/"


def test_expired_session_goes_back_to_the_same_page(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("trainer1@robomy.test")
    util.set_fake_now(util.now_ts() + 13 * 3600)       # sessions last 12 hours without a visit
    r = b.get(f"/b/{ids['A']}")
    assert r["statusCode"] == 303 and r["headers"]["location"] == f"/signin?next=%2Fb%2F{ids['A']}"


def test_form_sent_after_expiry_is_kept_and_offered_after_sign_in(aws):
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    b.get("/admin/institutions")
    util.set_fake_now(util.now_ts() + 13 * 3600)
    r = b.post("/admin/institutions", {"name": "Kept Institute", "code": "KI", "idem": "same-key",
                                       "back": "/admin/institutions"})
    assert r["statusCode"] == 303
    loc = r["headers"]["location"]
    assert loc.startswith("/signin?next=%2Fadmin%2Finstitutions&pending=")
    pending = loc.split("pending=")[1]
    assert org.list_institutions() == []                   # nothing saved without a session
    page = b.get(loc)
    assert "what you typed is kept" in body(page)
    r = b.post("/signin", {"local_user": "boss@robomy.test", "next": "/admin/institutions", "pending": pending},
               csrf=False)
    assert r["headers"]["location"].startswith(f"/resume/{pending}")
    page = b.get(r["headers"]["location"])
    assert "Kept Institute" in body(page) and "Save it now" in body(page)
    r = b.post("/admin/institutions", {"name": "Kept Institute", "code": "KI", "idem": "same-key"})
    assert r["statusCode"] == 303
    assert [i["name"] for i in org.list_institutions()] == ["Kept Institute"]


def test_sign_out_ends_the_session(aws):
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    sid = b.cookies["so_sid"]
    b.post("/signout", {})
    assert "so_sid" not in b.cookies
    b.cookies["so_sid"] = sid                              # an old cookie no longer works
    assert b.get("/admin")["statusCode"] == 303


def test_local_sign_in_is_impossible_outside_local_mode(cognito):
    r = Browser().post("/signin", {"local_user": "boss@robomy.test"}, csrf=False)
    assert r["statusCode"] in (400, 401)
    assert "so_sid" not in Browser().cookies
    assert auth.local_users  # still importable, but unused in AWS
