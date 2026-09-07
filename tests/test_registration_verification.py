from app import app, _registration_verification_status


def test_registration_verification_status_maps_adapter_results():
    assert _registration_verification_status(
        {"is_valid": True, "source_mode": "MOCK", "message": "active"}, "GSTIN"
    )["state"] == "VERIFIED"
    assert _registration_verification_status(
        {"is_valid": False, "source_mode": "MOCK", "message": "not found"}, "PAN"
    )["state"] == "ISSUE"
    assert _registration_verification_status(
        {"is_valid": None, "source_mode": "UNAVAILABLE", "message": "unavailable"}, "GSTIN"
    )["state"] == "UNVERIFIED"


def test_registration_rejects_unverified_identifiers(monkeypatch):
    monkeypatch.setenv("WTF_CSRF_ENABLED", "False")
    monkeypatch.setenv("GST_MODE", "UNAVAILABLE")
    monkeypatch.setenv("PAN_MODE", "UNAVAILABLE")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_client() as client:
        response = client.post("/register", data={
            "name": "Test Supplier",
            "email": "new-supplier@example.test",
            "password": "password123",
            "phone": "+91 99999 99999",
            "company_name": "Test Supplier Private Limited",
            "gstin": "07INVALIDGSTIN",
            "pan": "INVALIDPAN",
            "registered_address": "Test address, New Delhi 110001",
        })

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "UNVERIFIED" in body
    assert "Source mode: UNAVAILABLE" in body
