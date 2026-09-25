"""A questionnaire with a repeated question id is refused before any run starts.

The report keys answers by id: with two q3s it printed the scale question's
ratings under the open question's prompt and dropped the open answers. The
wizard produced such ids after a deletion ('q' + (length + 1)), and inserting
SUS twice repeated sus1..sus10; any other client can send them too.
"""
from simulated_web_agent.main import app as app_module


def _payload(ids):
    return {"total_personas": 1, "demographics": [], "general_intent": "Zadanie",
            "start_url": "https://example.com", "max_steps": 5,
            "questionnaire": {"questions": [{"id": i, "type": "short_text", "prompt": i}
                                            for i in ids]}}


def test_repeated_ids_are_a_bad_request(monkeypatch):
    monkeypatch.setattr(app_module, "_API_KEY", "")
    app_module.app.config.update(TESTING=True)
    res = app_module.app.test_client().post("/run", json=_payload(["q2", "q3", "q3"]))
    assert res.status_code == 400
    assert "q3" in res.get_data(as_text=True)
