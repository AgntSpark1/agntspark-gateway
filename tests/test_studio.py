"""Studio: no-code assistants, knowledge, test and public chat, reply allowance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from agntspark_core.auth import Role
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agntspark_gateway import db as db_module
from agntspark_gateway.config import settings
from agntspark_gateway.exceptions import StudioModelError
from agntspark_gateway.main import create_app
from agntspark_gateway.models.studio import StudioUsageMonth
from agntspark_gateway.models.user import User
from agntspark_gateway.routers import public_chat
from agntspark_gateway.security.passwords import hash_password
from agntspark_gateway.studio import knowledge
from agntspark_gateway.studio.limits import STUDIO_LIMITS
from agntspark_gateway.studio.llm import ChatReply, get_chat_model

pytestmark = pytest.mark.integration

PASSWORD = "hunter2hunter2"


@dataclass
class FakeModel:
    calls: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)
    fail: bool = False

    async def reply(self, *, system: str, messages: list[dict[str, str]]) -> ChatReply:
        if self.fail:
            raise StudioModelError()
        self.calls.append((system, messages))
        return ChatReply(text=f"reply {len(self.calls)}", input_tokens=100, output_tokens=20)


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()


@pytest.fixture(autouse=True)
def _reset_visitor_limits() -> None:
    public_chat.per_visitor.reset()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, model: FakeModel) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[db_module.get_db] = _override_get_db
    app.dependency_overrides[get_chat_model] = lambda: model
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _login(
    client: AsyncClient,
    db: AsyncSession,
    email: str,
    *,
    plan: str = "free",
    role: Role = Role.OPERATOR,
) -> dict[str, str]:
    db.add(
        User(
            email=email, password_hash=hash_password(PASSWORD), name="B", plan=plan, role=int(role)
        )
    )
    await db.commit()
    resp = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _assistant(client: AsyncClient, headers: dict[str, str], **body: str) -> dict:
    payload = {"template": "customer-support", "name": "Bakery Helper", **body}
    resp = await client.post("/v1/studio/assistants", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestAssistants:
    async def test_templates_are_listed(self, client: AsyncClient) -> None:
        resp = await client.get("/v1/studio/templates")
        assert resp.status_code == 200
        keys = [t["key"] for t in resp.json()]
        assert keys == ["customer-support", "personal-assistant", "knowledge-qa"]

    async def test_create_uses_template_greeting_and_starts_unpublished(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "create@studio.agntspark.com")
        a = await _assistant(client, headers)
        assert a["greeting"] == "Hi! How can I help you today?"
        assert a["is_public"] is False
        assert a["slug"].startswith("bakery-helper-")
        listed = (await client.get("/v1/studio/assistants", headers=headers)).json()
        assert [x["id"] for x in listed] == [a["id"]]

    async def test_create_keeps_a_greeting_in_the_owners_language(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "greet@studio.agntspark.com")
        resp = await client.post(
            "/v1/studio/assistants",
            json={
                "template": "customer-support",
                "name": "阳光烘焙",
                "greeting": "你好！有什么可以帮你？",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["greeting"] == "你好！有什么可以帮你？"

    async def test_unknown_template_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "tpl@studio.agntspark.com")
        resp = await client.post(
            "/v1/studio/assistants", json={"template": "nope", "name": "X"}, headers=headers
        )
        assert resp.status_code == 422

    async def test_other_accounts_cannot_see_it(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner = await _login(client, db_session, "owner@studio.agntspark.com")
        other = await _login(client, db_session, "other@studio.agntspark.com")
        a = await _assistant(client, owner)
        resp = await client.get(f"/v1/studio/assistants/{a['id']}", headers=other)
        assert resp.status_code == 404
        resp = await client.patch(
            f"/v1/studio/assistants/{a['id']}", json={"name": "Mine"}, headers=other
        )
        assert resp.status_code == 404

    async def test_viewers_cannot_build(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "viewer@studio.agntspark.com", role=Role.VIEWER)
        resp = await client.post(
            "/v1/studio/assistants",
            json={"template": "customer-support", "name": "X"},
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_assistant_count_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "count@studio.agntspark.com")
        for i in range(STUDIO_LIMITS["free"].max_assistants):
            await _assistant(client, headers, name=f"A{i}")
        resp = await client.post(
            "/v1/studio/assistants",
            json={"template": "customer-support", "name": "One too many"},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["details"]["resource"] == "assistants"

    async def test_update_and_delete(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await _login(client, db_session, "edit@studio.agntspark.com")
        a = await _assistant(client, headers)
        resp = await client.patch(
            f"/v1/studio/assistants/{a['id']}",
            json={"instructions": "Open 8-6.", "is_public": True},
            headers=headers,
        )
        assert resp.json()["instructions"] == "Open 8-6."
        assert resp.json()["is_public"] is True
        assert (
            await client.delete(f"/v1/studio/assistants/{a['id']}", headers=headers)
        ).status_code == 204
        assert (
            await client.get(f"/v1/studio/assistants/{a['id']}", headers=headers)
        ).status_code == 404


class TestKnowledge:
    def test_split_packs_paragraphs_and_cuts_long_ones(self) -> None:
        text = "First para.\n\nSecond para.\n\n" + ("word " * 600)
        chunks = knowledge.split_into_chunks(text, size=200)
        assert chunks[0] == "First para.\n\nSecond para."
        assert all(len(c) <= 200 for c in chunks)
        assert "".join(chunks).count("word") == 600

    async def test_documents_feed_the_prompt(
        self, client: AsyncClient, db_session: AsyncSession, model: FakeModel
    ) -> None:
        headers = await _login(client, db_session, "kb@studio.agntspark.com")
        a = await _assistant(client, headers, instructions="We are a bakery in Austin.")
        resp = await client.post(
            f"/v1/studio/assistants/{a['id']}/documents",
            json={"title": "Delivery", "content": "Delivery is free for orders over $40."},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["chars"] == len("Delivery is free for orders over $40.")
        assert (await client.get(f"/v1/studio/assistants/{a['id']}", headers=headers)).json()[
            "documents"
        ] == 1

        await client.post(
            f"/v1/studio/assistants/{a['id']}/chat",
            json={"message": "Do you deliver?"},
            headers=headers,
        )
        system, _ = model.calls[0]
        assert "We are a bakery in Austin." in system
        assert '<document title="Delivery">' in system
        assert "free for orders over $40" in system
        # Visitors write in their own language; the reply follows them, not the owner's.
        assert "Reply in the language the person writes in" in system

    async def test_large_knowledge_retrieves_matching_passages(
        self, client: AsyncClient, db_session: AsyncSession, model: FakeModel
    ) -> None:
        headers = await _login(client, db_session, "bigkb@studio.agntspark.com")
        a = await _assistant(client, headers)
        filler = "\n\n".join(f"Section {i}: " + ("lorem ipsum " * 90) for i in range(12))
        await client.post(
            f"/v1/studio/assistants/{a['id']}/documents",
            json={"title": "Handbook", "content": filler},
            headers=headers,
        )
        await client.post(
            f"/v1/studio/assistants/{a['id']}/documents",
            json={"title": "Refunds", "content": "Refunds are issued within 14 days."},
            headers=headers,
        )
        await client.post(
            f"/v1/studio/assistants/{a['id']}/chat",
            json={"message": "How do refunds work?"},
            headers=headers,
        )
        system, _ = model.calls[0]
        assert "Refunds are issued within 14 days." in system
        assert "lorem ipsum" not in system

    async def test_knowledge_limit(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await _login(client, db_session, "kblimit@studio.agntspark.com")
        a = await _assistant(client, headers)
        monkeypatch.setitem(
            STUDIO_LIMITS,
            "free",
            STUDIO_LIMITS["free"].__class__(
                max_assistants=3, max_messages_month=100, max_knowledge_chars=10
            ),
        )
        resp = await client.post(
            f"/v1/studio/assistants/{a['id']}/documents",
            json={"title": "Too long", "content": "x" * 11},
            headers=headers,
        )
        assert resp.status_code == 403
        assert resp.json()["details"]["resource"] == "knowledge_chars"

    async def test_delete_document(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await _login(client, db_session, "kbdel@studio.agntspark.com")
        a = await _assistant(client, headers)
        doc = (
            await client.post(
                f"/v1/studio/assistants/{a['id']}/documents",
                json={"title": "Hours", "content": "Open 8-6."},
                headers=headers,
            )
        ).json()
        resp = await client.delete(
            f"/v1/studio/assistants/{a['id']}/documents/{doc['id']}", headers=headers
        )
        assert resp.status_code == 204
        docs = await client.get(f"/v1/studio/assistants/{a['id']}/documents", headers=headers)
        assert docs.json() == []


class TestChat:
    async def test_conversation_continues_and_is_kept(
        self, client: AsyncClient, db_session: AsyncSession, model: FakeModel
    ) -> None:
        headers = await _login(client, db_session, "chat@studio.agntspark.com")
        a = await _assistant(client, headers)
        url = f"/v1/studio/assistants/{a['id']}/chat"
        first = (await client.post(url, json={"message": "Hi"}, headers=headers)).json()
        assert first["reply"]["content"] == "reply 1"
        cid = first["conversation_id"]
        await client.post(url, json={"message": "Again", "conversation_id": cid}, headers=headers)

        _, sent = model.calls[1]
        assert sent == [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "reply 1"},
            {"role": "user", "content": "Again"},
        ]
        convo = (
            await client.get(
                f"/v1/studio/assistants/{a['id']}/conversations/{cid}", headers=headers
            )
        ).json()
        assert [m["content"] for m in convo["messages"]] == ["Hi", "reply 1", "Again", "reply 2"]
        listed = (
            await client.get(f"/v1/studio/assistants/{a['id']}/conversations", headers=headers)
        ).json()
        assert listed[0]["messages"] == 4
        assert listed[0]["preview"] == "Hi"
        assert listed[0]["source"] == "test"

    async def test_usage_counts_replies_and_tokens(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "usage@studio.agntspark.com")
        a = await _assistant(client, headers)
        for _ in range(2):
            await client.post(
                f"/v1/studio/assistants/{a['id']}/chat", json={"message": "Hi"}, headers=headers
            )
        usage = (await client.get("/v1/studio/usage", headers=headers)).json()
        assert usage["messages_used"] == 2
        assert usage["messages_limit"] == STUDIO_LIMITS["free"].max_messages_month
        assert usage["assistants"] == 1
        row = (await db_session.execute(select(StudioUsageMonth))).scalars().all()
        assert [(r.messages, r.input_tokens, r.output_tokens) for r in row] == [(2, 200, 40)]

    async def test_monthly_allowance_stops_replies(
        self, client: AsyncClient, db_session: AsyncSession, model: FakeModel
    ) -> None:
        headers = await _login(client, db_session, "allowance@studio.agntspark.com")
        a = await _assistant(client, headers)
        user = (
            await db_session.execute(
                select(User).where(User.email == "allowance@studio.agntspark.com")
            )
        ).scalar_one()
        from agntspark_gateway.services.metering_service import month_start

        db_session.add(
            StudioUsageMonth(
                user_id=user.id,
                month_start=month_start(),
                messages=STUDIO_LIMITS["free"].max_messages_month,
            )
        )
        await db_session.commit()
        resp = await client.post(
            f"/v1/studio/assistants/{a['id']}/chat", json={"message": "Hi"}, headers=headers
        )
        assert resp.status_code == 403
        assert resp.json()["details"]["resource"] == "messages_month"
        assert model.calls == []

    async def test_admins_are_exempt(self, client: AsyncClient, db_session: AsyncSession) -> None:
        headers = await _login(client, db_session, "admin@studio.agntspark.com", role=Role.ADMIN)
        for i in range(STUDIO_LIMITS["free"].max_assistants + 1):
            await _assistant(client, headers, name=f"A{i}")

    async def test_model_failure_stores_nothing(
        self, client: AsyncClient, db_session: AsyncSession, model: FakeModel
    ) -> None:
        headers = await _login(client, db_session, "fail@studio.agntspark.com")
        a = await _assistant(client, headers)
        model.fail = True
        resp = await client.post(
            f"/v1/studio/assistants/{a['id']}/chat", json={"message": "Hi"}, headers=headers
        )
        assert resp.status_code == 502
        listed = await client.get(f"/v1/studio/assistants/{a['id']}/conversations", headers=headers)
        assert listed.json() == []
        usage = (await client.get("/v1/studio/usage", headers=headers)).json()
        assert usage["messages_used"] == 0

    async def test_no_model_key_is_a_503(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "studio_anthropic_api_key", None)
        app = create_app()

        async def _override_get_db() -> AsyncIterator[AsyncSession]:
            yield db_session

        app.dependency_overrides[db_module.get_db] = _override_get_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = await _login(ac, db_session, "nokey@studio.agntspark.com")
            a = await _assistant(ac, headers)
            resp = await ac.post(
                f"/v1/studio/assistants/{a['id']}/chat", json={"message": "Hi"}, headers=headers
            )
            assert resp.status_code == 503
            assert resp.json()["code"] == "STUDIO_NOT_CONFIGURED"
            usage = (await ac.get("/v1/studio/usage", headers=headers)).json()
            assert usage["chat_available"] is False


class TestPublicChat:
    async def _published(self, client: AsyncClient, db: AsyncSession, email: str) -> dict:
        headers = await _login(client, db, email)
        a = await _assistant(client, headers)
        await client.patch(
            f"/v1/studio/assistants/{a['id']}", json={"is_public": True}, headers=headers
        )
        return {**a, "headers": headers}

    async def test_unpublished_assistant_is_hidden(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await _login(client, db_session, "hidden@studio.agntspark.com")
        a = await _assistant(client, headers)
        assert (await client.get(f"/v1/public/assistants/{a['slug']}")).status_code == 404
        resp = await client.post(f"/v1/public/assistants/{a['slug']}/chat", json={"message": "Hi"})
        assert resp.status_code == 404

    async def test_visitor_chats_without_login_and_owner_sees_it(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        a = await self._published(client, db_session, "public@studio.agntspark.com")
        info = (await client.get(f"/v1/public/assistants/{a['slug']}")).json()
        assert info == {"name": "Bakery Helper", "greeting": "Hi! How can I help you today?"}

        first = (
            await client.post(f"/v1/public/assistants/{a['slug']}/chat", json={"message": "Hours?"})
        ).json()
        cid = first["conversation_id"]
        restored = (
            await client.get(f"/v1/public/assistants/{a['slug']}/conversations/{cid}")
        ).json()
        assert [m["role"] for m in restored["messages"]] == ["user", "assistant"]

        listed = (
            await client.get(f"/v1/studio/assistants/{a['id']}/conversations", headers=a["headers"])
        ).json()
        assert listed[0]["source"] == "public"
        usage = (await client.get("/v1/studio/usage", headers=a["headers"])).json()
        assert usage["messages_used"] == 1

    async def test_visitor_cannot_continue_a_test_conversation(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        a = await self._published(client, db_session, "mix@studio.agntspark.com")
        test = (
            await client.post(
                f"/v1/studio/assistants/{a['id']}/chat",
                json={"message": "Owner test"},
                headers=a["headers"],
            )
        ).json()
        resp = await client.post(
            f"/v1/public/assistants/{a['slug']}/chat",
            json={"message": "Hi", "conversation_id": test["conversation_id"]},
        )
        assert resp.status_code == 404
        resp = await client.get(
            f"/v1/public/assistants/{a['slug']}/conversations/{test['conversation_id']}"
        )
        assert resp.status_code == 404

    async def test_visitor_rate_limit(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "studio_public_rpm_per_ip", 2)
        a = await self._published(client, db_session, "rate@studio.agntspark.com")
        url = f"/v1/public/assistants/{a['slug']}/chat"
        for _ in range(2):
            assert (await client.post(url, json={"message": "Hi"})).status_code == 200
        resp = await client.post(url, json={"message": "Hi"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    async def test_blank_message_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        a = await self._published(client, db_session, "blank@studio.agntspark.com")
        resp = await client.post(f"/v1/public/assistants/{a['slug']}/chat", json={"message": "  "})
        assert resp.status_code == 422
