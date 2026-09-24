import os
import tempfile
import unittest

os.environ["MODERATOR_PASSWORD"] = "test-password-very-long"
_temp = tempfile.TemporaryDirectory()
os.environ["QUIET_STAGE_DATA_DIR"] = _temp.name

from fastapi.testclient import TestClient
from backend.main import app


class QuietStageApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        _temp.cleanup()

    def test_full_text_submission_flow(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        homepage = self.client.get("/")
        self.assertEqual(homepage.status_code, 200)
        self.assertIn("Твоим словам", homepage.text)
        self.assertIn('id="page-experts"', homepage.text)
        self.assertIn('id="page-privacy"', homepage.text)
        self.assertIn('id="page-rights"', homepage.text)
        self.assertIn('data-form-step="1"', homepage.text)
        self.assertIn('data-form-step="3"', homepage.text)
        self.assertIn('id="questionForm"', homepage.text)
        self.assertIn('id="expertApplicationForm"', homepage.text)
        self.assertIn("https://vk.ru/gydo_lnr_rzxet", homepage.text)
        self.assertEqual(self.client.get("/redesign.css").status_code, 200)
        self.assertEqual(self.client.get("/assets/hero-atelier.svg").status_code, 200)
        self.assertEqual(self.client.get("/data/quiet_stage.db").status_code, 404)
        self.assertEqual(self.client.get("/backend/main.py").status_code, 404)

        rejected = self.client.post("/api/moderator/login", json={"password": "wrong"})
        self.assertEqual(rejected.status_code, 401)

        created = self.client.post(
            "/api/submissions",
            data={
                "alias": "Тестовый автор",
                "ageGroup": "18–22 года",
                "format": "Текст",
                "genre": "Поэзия",
                "title": "Тестовая работа",
                "body": "Это демонстрационный текст достаточной длины для проверки.",
                "author": "true",
                "voluntary": "true",
                "personalData": "true",
                "copyrightTerms": "true",
                "demo": "true",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        access_code = created.json()["accessCode"]

        status = self.client.get(f"/api/submissions/status/{access_code}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "submitted")

        too_early = self.client.post(
            "/api/submissions/next-step",
            json={"accessCode": access_code, "nextStep": "Доработать работу"},
        )
        self.assertEqual(too_early.status_code, 409)

        login = self.client.post(
            "/api/moderator/login", json={"password": "test-password-very-long"}
        )
        self.assertEqual(login.status_code, 200)
        csrf = login.json()["csrfToken"]

        queue = self.client.get("/api/moderator/submissions")
        self.assertEqual(queue.status_code, 200)
        submission_id = queue.json()["items"][0]["id"]

        no_csrf = self.client.post(
            f"/api/moderator/submissions/{submission_id}/review",
            json={"feedback": "Содержательный тестовый отзыв для автора."},
        )
        self.assertEqual(no_csrf.status_code, 403)

        reviewed = self.client.post(
            f"/api/moderator/submissions/{submission_id}/review",
            headers={"X-CSRF-Token": csrf},
            json={"feedback": "Содержательный тестовый отзыв для автора."},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)

        status = self.client.get(f"/api/submissions/status/{access_code}").json()
        self.assertEqual(status["status"], "reviewed")
        self.assertIn("тестовый отзыв", status["feedback"])

        next_step = self.client.post(
            "/api/submissions/next-step",
            json={"accessCode": access_code, "nextStep": "Показать пилотной группе"},
        )
        self.assertEqual(next_step.status_code, 200)
        final = self.client.get(f"/api/submissions/status/{access_code}").json()
        self.assertEqual(final["status"], "shown")
        self.assertEqual(final["nextStep"], "Показать пилотной группе")

    def test_required_consents(self):
        base = {
            "alias": "Юный автор",
            "ageGroup": "14–17 лет",
            "format": "Текст",
            "genre": "Проза",
            "title": "Проверка согласий",
            "body": "Демонстрационный текст для проверки обязательных согласий.",
            "author": "true",
            "voluntary": "true",
            "personalData": "true",
            "copyrightTerms": "true",
            "demo": "true",
        }
        no_guardian = self.client.post("/api/submissions", data=base)
        self.assertEqual(no_guardian.status_code, 422)

        no_personal_data = dict(base, guardianConsent="true")
        del no_personal_data["personalData"]
        rejected = self.client.post("/api/submissions", data=no_personal_data)
        self.assertEqual(rejected.status_code, 422)

    def test_audio_validation_and_private_download(self):
        common = {
            "alias": "Аудио автор",
            "ageGroup": "14–17 лет",
            "format": "Аудио",
            "genre": "Песня",
            "title": "Аудиопроба",
            "body": "",
            "author": "true",
            "voluntary": "true",
            "personalData": "true",
            "copyrightTerms": "true",
            "guardianConsent": "true",
            "demo": "true",
        }
        rejected = self.client.post(
            "/api/submissions",
            data=common,
            files={"audio": ("unsafe.exe", b"not-audio", "application/octet-stream")},
        )
        self.assertEqual(rejected.status_code, 415)

        created = self.client.post(
            "/api/submissions",
            data=common,
            files={"audio": ("demo.mp3", b"ID3-demo-audio", "audio/mpeg")},
        )
        self.assertEqual(created.status_code, 201, created.text)

        unauthenticated = TestClient(app)
        self.assertEqual(
            unauthenticated.get("/api/moderator/submissions/unknown/audio").status_code,
            401,
        )

        login = self.client.post(
            "/api/moderator/login", json={"password": "test-password-very-long"}
        )
        self.assertEqual(login.status_code, 200)
        items = self.client.get("/api/moderator/submissions").json()["items"]
        audio_item = next(item for item in items if item["title"] == "Аудиопроба")
        downloaded = self.client.get(
            f"/api/moderator/submissions/{audio_item['id']}/audio"
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, b"ID3-demo-audio")

    def test_contact_and_expert_application(self):
        no_consent = self.client.post(
            "/api/contact",
            json={
                "name": "Посетитель",
                "email": "visitor@example.ru",
                "message": "Подскажите, как принять участие?",
                "personalData": False,
            },
        )
        self.assertEqual(no_consent.status_code, 422)

        contact = self.client.post(
            "/api/contact",
            json={
                "name": "Посетитель",
                "email": "visitor@example.ru",
                "message": "Подскажите, как принять участие?",
                "personalData": True,
            },
        )
        self.assertEqual(contact.status_code, 201, contact.text)

        expert = self.client.post(
            "/api/expert-applications",
            json={
                "name": "Тестовый эксперт",
                "email": "expert@example.ru",
                "role": "Редактор текста",
                "experience": "Более пяти лет редактирую тексты молодых авторов.",
                "portfolio": "https://example.ru/portfolio",
                "motivation": "Хочу давать авторам бережную и содержательную обратную связь.",
                "personalData": True,
            },
        )
        self.assertEqual(expert.status_code, 201, expert.text)

        login = self.client.post(
            "/api/moderator/login", json={"password": "test-password-very-long"}
        )
        self.assertEqual(login.status_code, 200)
        inbox = self.client.get("/api/moderator/submissions").json()
        self.assertTrue(any(item["email"] == "visitor@example.ru" for item in inbox["contactRequests"]))
        self.assertTrue(any(item["email"] == "expert@example.ru" for item in inbox["expertApplications"]))


if __name__ == "__main__":
    unittest.main()
