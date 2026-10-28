import io
import json
import os
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage

import app as app_module


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class NonSeekableStream(io.BytesIO):
    def tell(self):
        raise OSError("stream is not seekable")

    def seek(self, *_args):
        raise OSError("stream is not seekable")


class RestoreOnceFailingStream(io.BytesIO):
    """Seek to end works, the first restore fails, then recovery can succeed."""

    def __init__(self, initial_bytes):
        super().__init__(initial_bytes)
        self._end_seek_seen = False
        self._restore_failed = False

    def seek(self, offset, whence=os.SEEK_SET):
        if whence == os.SEEK_END:
            self._end_seek_seen = True
            return super().seek(offset, whence)
        if self._end_seek_seen and not self._restore_failed:
            self._restore_failed = True
            raise OSError("simulated restore failure")
        return super().seek(offset, whence)


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, _tag, attrs):
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.append(element_id)


class AppRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config.update(TESTING=True, MAX_CONTENT_LENGTH=100 * 1024 * 1024)

    def setUp(self):
        self.client = app_module.app.test_client()

    def test_error_message_handles_string_error_and_invalid_json(self):
        self.assertEqual(
            app_module._extract_api_error_message(
                FakeResponse(400, {"error": "bad request"})
            ),
            "bad request",
        )
        self.assertEqual(
            app_module._extract_api_error_message(
                FakeResponse(502, ValueError(), "upstream down")
            ),
            "upstream down",
        )

    def test_split_and_context_preserve_text(self):
        source = ("第一句。" * 4) + ("第二句！" * 4)
        chunks = app_module._split_text_intelligently(source, chunk_size=10)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(chunk.strip() for chunk in chunks))
        self.assertEqual(
            app_module._get_last_sentence("前文。最后一句。"), "最后一句。"
        )
        self.assertEqual(app_module._get_last_sentence("没有句号"), "没有句号")

        for invalid_size in (0, -1, 1.5, True):
            with self.subTest(chunk_size=invalid_size):
                with self.assertRaises(ValueError):
                    app_module._split_text_intelligently(
                        "文本", chunk_size=invalid_size
                    )

    def test_split_preserves_whitespace_and_prefers_word_boundaries(self):
        source = "alpha beta gamma delta"
        chunks = app_module._split_text_intelligently(source, chunk_size=11)
        self.assertEqual("".join(chunks), source)
        self.assertEqual(chunks[0], "alpha beta ")
        self.assertTrue(all(len(chunk) <= 11 for chunk in chunks))

        boundary_source = ("A" * 11) + "。" + ("B" * 4)
        boundary_chunks = app_module._split_text_intelligently(
            boundary_source, chunk_size=11
        )
        self.assertEqual("".join(boundary_chunks), boundary_source)
        self.assertTrue(all(len(chunk) <= 11 for chunk in boundary_chunks))

        whitespace_source = "A" + (" " * 25) + "B"
        whitespace_chunks = app_module._split_text_intelligently(
            whitespace_source, chunk_size=8
        )
        self.assertEqual("".join(whitespace_chunks), whitespace_source)

    def test_optimization_preserves_long_whitespace_without_calling_upstream_for_blank_chunks(
        self,
    ):
        source = "start." + (" " * 40) + "end."
        seen_payloads = []
        seen_user_messages = []

        def fake_chat(messages, *_args, **_kwargs):
            user_message = messages[-1]["content"]
            seen_user_messages.append(user_message)
            payload = user_message
            if "---TEXT TO CALIBRATE---" in payload:
                payload = payload.split("---TEXT TO CALIBRATE---\n", 1)[1].rsplit(
                    "\n---END TEXT---", 1
                )[0]
            seen_payloads.append(payload)
            self.assertTrue(payload.strip())
            return {"status": "success", "content": payload}

        with (
            patch.object(app_module, "OPT_API_KEY", "key"),
            patch.object(app_module, "OPT_API_URL", "https://example.test/chat"),
            patch.object(app_module, "CALIBRATION_MODEL", "model"),
            patch.object(app_module, "CHUNK_TARGET_SIZE", 8),
            patch.object(app_module, "CHUNK_PROCESSING_THRESHOLD", 8),
            patch.object(app_module, "_request_chat_completion", side_effect=fake_chat),
        ):
            text, message, calibrated = app_module._perform_text_optimization(source)

        self.assertTrue(calibrated, message)
        self.assertEqual(text, source)
        self.assertTrue(seen_payloads)
        self.assertTrue(all(payload.strip() for payload in seen_payloads))
        self.assertTrue(
            any("---CONTEXT---\nstart." in message for message in seen_user_messages),
            "有效文本块之间的上下文不应被纯空白块截断",
        )

    def test_summary_and_notes_skip_separator_only_chunks(self):
        source = "alpha." + (" " * 40) + "omega."
        seen_summary_chunks = []
        seen_note_chunks = []

        def fake_summary(chunk):
            seen_summary_chunks.append(chunk)
            self.assertTrue(chunk.strip())
            return {"status": "success", "content": "- 要点"}

        def fake_note_transform(text, *_args):
            seen_note_chunks.append(text)
            inner = text.split("<待处理文本>\n", 1)[1].rsplit("\n</待处理文本>", 1)[0]
            self.assertTrue(inner.strip())
            return {"status": "success", "content": "# 笔记"}

        with (
            patch.object(app_module, "OPT_API_KEY", "key"),
            patch.object(app_module, "OPT_API_URL", "https://example.test/chat"),
            patch.object(app_module, "SUMMARY_MODEL", "summary-model"),
            patch.object(app_module, "NOTES_MODEL", "notes-model"),
            patch.object(app_module, "CHUNK_TARGET_SIZE", 8),
            patch.object(
                app_module, "_summarize_chunk_with_retry", side_effect=fake_summary
            ),
            patch.object(
                app_module,
                "_reduce_text_fragments",
                return_value={"status": "success", "content": "最终结果"},
            ),
        ):
            summary = app_module._perform_summarization(source)

        with (
            patch.object(app_module, "OPT_API_KEY", "key"),
            patch.object(app_module, "OPT_API_URL", "https://example.test/chat"),
            patch.object(app_module, "NOTES_MODEL", "notes-model"),
            patch.object(app_module, "CHUNK_TARGET_SIZE", 8),
            patch.object(
                app_module,
                "_request_text_transformation",
                side_effect=fake_note_transform,
            ),
            patch.object(
                app_module,
                "_reduce_text_fragments",
                return_value={"status": "success", "content": "最终笔记"},
            ),
        ):
            notes = app_module._perform_notes_generation(source)

        self.assertEqual(summary, {"status": "success", "summary": "最终结果"})
        self.assertEqual(notes, {"status": "success", "notes": "最终笔记"})
        self.assertTrue(seen_summary_chunks)
        self.assertTrue(seen_note_chunks)

    def test_bounded_worker_map_avoids_pool_for_single_item(self):
        with patch.object(app_module, "ThreadPoolExecutor") as executor:
            result = app_module._map_with_bounded_workers(lambda value: value * 2, [3])
        self.assertEqual(result, [6])
        executor.assert_not_called()

    def test_docker_healthcheck_uses_runtime_port(self):
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("os.environ.get('PORT', '5000')", dockerfile)
        self.assertNotIn("http://127.0.0.1:5000/healthz", dockerfile)

    def test_healthcheck_and_page_use_current_repository(self):
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["status"], "ok")
        self.assertEqual(health.get_json()["max_text_chars"], app_module.MAX_TEXT_CHARS)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("https://github.com/Qianxia666/siliconflow-speech-to-text", body)
        self.assertNotIn("xy2yp", body)
        self.assertIn(f'data-max-text-chars="{app_module.MAX_TEXT_CHARS}"', body)
        self.assertIn("<title>音频转录校准工具</title>", body)

    def test_api_method_and_path_errors_are_json(self):
        not_found = self.client.get("/api/does-not-exist")
        method_error = self.client.get("/api/transcribe")
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(method_error.status_code, 405)
        self.assertEqual(not_found.get_json()["error"]["type"], "invalid_request_error")
        self.assertEqual(
            method_error.get_json()["error"]["type"], "invalid_request_error"
        )

    def test_browser_responses_include_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn(
            "frame-ancestors 'none'", response.headers["Content-Security-Policy"]
        )
        self.assertNotIn(
            "fonts.googleapis.com", response.headers["Content-Security-Policy"]
        )

    def test_frontend_workbench_is_self_contained_and_has_unique_controls(self):
        response = self.client.get("/")
        body = response.get_data(as_text=True)
        parser = IdCollector()
        parser.feed(body)

        self.assertNotIn("tailwind", body.lower())
        self.assertNotIn("fonts.googleapis.com", body)
        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertTrue(
            {
                "transcriptionForm",
                "audioFile",
                "sourceTab",
                "summaryTab",
                "notesTab",
                "resultContent",
                "transcriptionResult",
            }.issubset(parser.ids)
        )
        self.assertIn('role="tablist"', body)
        self.assertIn('data-testid="workspace"', body)

    def test_frontend_enforces_server_text_processing_limit(self):
        script = (
            Path(__file__).resolve().parents[1] / "static" / "script.js"
        ).read_text(encoding="utf-8")
        self.assertIn("document.body.dataset.maxTextChars", script)
        self.assertIn("function countCharacters(text)", script)
        self.assertIn(
            "countCharacters(currentCalibratedText) <= MAX_TEXT_CHARS", script
        )
        self.assertIn(
            "countCharacters(currentRawTranscription) <= MAX_TEXT_CHARS", script
        )

    def test_frontend_exposes_s2t_configuration_state(self):
        with (
            patch.object(app_module, "S2T_API_KEY", "key"),
            patch.object(app_module, "S2T_MODEL", "model"),
            patch.object(app_module, "S2T_API_URL", "https://example.test/transcribe"),
        ):
            configured_body = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-s2t-configured="true"', configured_body)
        self.assertIn("语音转录", configured_body)
        self.assertIn("可用", configured_body)

    def test_legacy_static_urls_remain_available(self):
        for path in ("/style.css", "/script.js", "/favicon.svg"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data)
                response.close()

    def test_only_qianxia666_github_repository_is_referenced(self):
        root = Path(__file__).resolve().parents[1]
        github_urls = set()
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {
                ".py",
                ".js",
                ".css",
                ".html",
                ".md",
                ".txt",
                ".svg",
            }:
                content = path.read_text(encoding="utf-8")
                github_urls.update(
                    re.findall(r'https://github\.com/[^\s)\]"\']+', content)
                )
        self.assertEqual(github_urls, {app_module.REPOSITORY_URL})

    def test_healthcheck_recognizes_dedicated_models_and_exposes_upload_limit(self):
        with (
            patch.object(app_module, "OPT_API_KEY", "key"),
            patch.object(app_module, "OPT_API_URL", "https://example.test/chat"),
            patch.object(app_module, "OPT_MODEL", None),
            patch.object(app_module, "CALIBRATION_MODEL", None),
            patch.object(app_module, "SUMMARY_MODEL", "summary-model"),
            patch.object(app_module, "NOTES_MODEL", None),
            patch.object(app_module, "MAX_UPLOAD_MB", 17),
        ):
            payload = self.client.get("/healthz").get_json()
        self.assertTrue(payload["optimization_configured"])
        self.assertFalse(payload["calibration_configured"])
        self.assertTrue(payload["summary_configured"])
        self.assertFalse(payload["notes_configured"])
        self.assertEqual(payload["max_upload_mb"], 17)

    def test_text_environment_values_are_trimmed_and_empty_values_disabled(self):
        with patch.dict(
            app_module.os.environ,
            {"TEST_TEXT_ENV": "  value  ", "TEST_EMPTY_ENV": "   "},
            clear=False,
        ):
            self.assertEqual(app_module._text_env("TEST_TEXT_ENV"), "value")
            self.assertIsNone(app_module._text_env("TEST_EMPTY_ENV"))
        self.assertEqual(
            app_module._text_env("TEST_MISSING_ENV", "fallback"), "fallback"
        )

    def test_page_uses_server_upload_limit(self):
        with (
            patch.object(app_module, "MAX_UPLOAD_BYTES", 7 * 1024 * 1024),
            patch.object(app_module, "MAX_UPLOAD_MB", 7),
        ):
            body = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-max-upload-bytes="7340032"', body)
        self.assertIn('data-max-upload-mb="7"', body)

    def test_v1_auth_rejects_malformed_bearer_header(self):
        with patch.object(app_module, "API_ACCESS_TOKEN", "secret"):
            self.assertEqual(self.client.get("/v1/models").status_code, 401)
            self.assertEqual(
                self.client.get(
                    "/v1/models", headers={"Authorization": "Bearer"}
                ).status_code,
                401,
            )
            self.assertEqual(
                self.client.get(
                    "/v1/models", headers={"Authorization": "Bearer wrong"}
                ).status_code,
                401,
            )

            response = self.client.get(
                "/v1/models", headers={"Authorization": "Bearer secret"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                {item["owned_by"] for item in response.get_json()["data"]},
                {"Qianxia666"},
            )

    def test_v1_auth_reports_missing_server_configuration_as_unavailable(self):
        with patch.object(app_module, "API_ACCESS_TOKEN", None):
            response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error"]["type"], "server_error")

    def test_summarize_returns_configuration_error_without_calling_upstream(self):
        with (
            patch.object(app_module, "OPT_API_KEY", None),
            patch.object(app_module, "SUMMARY_MODEL", None),
            patch.object(app_module.requests, "post") as post,
        ):
            response = self.client.post(
                "/api/summarize", json={"text_to_summarize": "一段文本"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("配置不完整", response.get_json()["error"])
        post.assert_not_called()

    def test_notes_returns_configuration_error_as_unavailable(self):
        with (
            patch.object(app_module, "OPT_API_KEY", None),
            patch.object(app_module, "NOTES_MODEL", None),
            patch.object(app_module.requests, "post") as post,
        ):
            response = self.client.post(
                "/api/generatenote", json={"text_to_process": "一段文本"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("配置不完整", response.get_json()["error"])
        post.assert_not_called()

    def test_chat_content_supports_content_parts(self):
        payload = {
            "choices": [
                {
                    "message": {
                        "content": [{"text": "第一段"}, " + ", {"text": "第二段"}]
                    }
                }
            ]
        }
        self.assertEqual(app_module._extract_chat_content(payload), "第一段 + 第二段")

    def test_json_routes_return_400_for_invalid_content(self):
        self.assertEqual(
            self.client.post("/api/recalibrate", data="not-json").status_code, 400
        )
        self.assertEqual(
            self.client.post(
                "/api/summarize", json={"text_to_summarize": 42}
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/generatenote", json={"text_to_process": 42}
            ).status_code,
            400,
        )

    def test_json_routes_reject_oversized_text_before_processing(self):
        with (
            patch.object(app_module, "MAX_TEXT_CHARS", 3),
            patch.object(app_module, "_perform_text_optimization") as optimize,
            patch.object(app_module, "_perform_summarization") as summarize,
            patch.object(app_module, "_perform_notes_generation") as notes,
        ):
            recalibrate_response = self.client.post(
                "/api/recalibrate", json={"raw_transcription": "four"}
            )
            summarize_response = self.client.post(
                "/api/summarize", json={"text_to_summarize": "four"}
            )
            notes_response = self.client.post(
                "/api/generatenote", json={"text_to_process": "four"}
            )
        self.assertEqual(recalibrate_response.status_code, 413)
        self.assertEqual(summarize_response.status_code, 413)
        self.assertEqual(notes_response.status_code, 413)
        optimize.assert_not_called()
        summarize.assert_not_called()
        notes.assert_not_called()

    def test_chat_completion_retries_transient_failure(self):
        responses = [
            FakeResponse(503, {"error": {"message": "busy"}}),
            FakeResponse(200, {"choices": [{"message": {"content": "完成"}}]}),
        ]
        with (
            patch.object(app_module, "OPT_API_KEY", "key"),
            patch.object(app_module, "OPT_API_URL", "https://example.test/chat"),
            patch.object(app_module, "RETRY_ATTEMPTS", 2),
            patch.object(app_module.time, "sleep") as sleep,
            patch.object(app_module.requests, "post", side_effect=responses) as post,
        ):
            result = app_module._request_chat_completion([], "model", 0.1, "测试")
        self.assertEqual(result, {"status": "success", "content": "完成"})
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_hierarchical_reduction_bounds_each_request(self):
        request_sizes = []
        responses = iter(["甲", "乙", "丙", "最终结果"])

        def fake_transform(text, *_args):
            request_sizes.append(len(text))
            return {"status": "success", "content": next(responses)}

        with (
            patch.object(app_module, "CHUNK_TARGET_SIZE", 12),
            patch.object(
                app_module,
                "_request_text_transformation",
                side_effect=fake_transform,
            ),
        ):
            result = app_module._reduce_text_fragments(
                ["A" * 8, "B" * 8, "C" * 8],
                "prompt",
                "model",
                0.2,
                "测试归并",
            )
        self.assertEqual(result, {"status": "success", "content": "最终结果"})
        self.assertEqual(len(request_sizes), 4)
        self.assertTrue(all(size <= 12 for size in request_sizes))

    def test_long_notes_are_chunked_then_reduced(self):
        with (
            patch.object(app_module, "OPT_API_KEY", "key"),
            patch.object(app_module, "OPT_API_URL", "https://example.test/chat"),
            patch.object(app_module, "NOTES_MODEL", "notes-model"),
            patch.object(app_module, "CHUNK_TARGET_SIZE", 4),
            patch.object(
                app_module,
                "_request_text_transformation",
                return_value={"status": "success", "content": "分段笔记"},
            ) as transform,
            patch.object(
                app_module,
                "_reduce_text_fragments",
                return_value={"status": "success", "content": "最终笔记"},
            ) as reduce_fragments,
        ):
            result = app_module._perform_notes_generation("第一段。第二段。")
        self.assertEqual(result, {"status": "success", "notes": "最终笔记"})
        self.assertGreater(transform.call_count, 1)
        reduce_fragments.assert_called_once()

    def test_optimization_skips_text_over_processing_limit(self):
        with (
            patch.object(app_module, "MAX_TEXT_CHARS", 3),
            patch.object(app_module, "_optimize_chunk_with_retry") as optimize,
        ):
            text, message, calibrated = app_module._perform_text_optimization("four")
        self.assertEqual(text, "four")
        self.assertIn("校准已跳过", message)
        self.assertFalse(calibrated)
        optimize.assert_not_called()

    def test_web_transcription_falls_back_when_optimization_is_unconfigured(self):
        s2t_response = FakeResponse(200, {"text": "原始转录"})
        with (
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(app_module, "OPT_API_KEY", None),
            patch.object(
                app_module.requests, "post", return_value=s2t_response
            ) as post,
        ):
            response = self.client.post(
                "/api/transcribe",
                data={"audio_file": (io.BytesIO(b"audio"), "sample.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["raw_transcription"], "原始转录")
        self.assertEqual(payload["transcription"], "原始转录")
        post.assert_called_once()

    def test_web_transcription_rejects_empty_and_oversized_files_before_upstream(self):
        with (
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(app_module.requests, "post") as post,
        ):
            empty_response = self.client.post(
                "/api/transcribe",
                data={"audio_file": (io.BytesIO(b""), "empty.wav")},
                content_type="multipart/form-data",
            )
            with (
                patch.object(app_module, "MAX_UPLOAD_BYTES", 3),
                patch.object(app_module, "MAX_UPLOAD_MB", 1),
            ):
                oversized_response = self.client.post(
                    "/api/transcribe",
                    data={"audio_file": (io.BytesIO(b"four"), "large.wav")},
                    content_type="multipart/form-data",
                )
        self.assertEqual(empty_response.status_code, 400)
        self.assertIn("不能为空", empty_response.get_json()["error"])
        self.assertEqual(oversized_response.status_code, 413)
        post.assert_not_called()

    def test_uploaded_file_size_recovers_stream_after_restore_failure(self):
        stream = RestoreOnceFailingStream(b"audio-bytes")
        audio_file = FileStorage(
            stream=stream, filename="sample.wav", content_type="audio/wav"
        )
        self.assertIsNone(app_module._uploaded_file_size(audio_file))
        self.assertEqual(stream.tell(), 0)
        self.assertEqual(stream.read(), b"audio-bytes")

    def test_transcription_retries_retryable_status_and_rewinds_upload(self):
        audio_file = FileStorage(
            stream=io.BytesIO(b"audio-bytes"),
            filename="sample.wav",
            content_type="audio/wav",
        )
        received_bodies = []

        def fake_post(*_args, **kwargs):
            received_bodies.append(kwargs["files"]["file"][1].read())
            if len(received_bodies) == 1:
                return FakeResponse(503, {"error": "busy"})
            return FakeResponse(200, {"text": "重试成功"})

        with (
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(app_module, "RETRY_ATTEMPTS", 2),
            patch.object(app_module.requests, "post", side_effect=fake_post),
            patch.object(app_module.time, "sleep") as sleep,
        ):
            transcription = app_module._transcribe_audio_file(audio_file)
        self.assertEqual(transcription, "重试成功")
        self.assertEqual(received_bodies, [b"audio-bytes", b"audio-bytes"])
        sleep.assert_called_once_with(app_module.RETRY_BACKOFF_SECONDS)

    def test_transcription_does_not_retry_non_seekable_upload(self):
        audio_file = FileStorage(
            stream=NonSeekableStream(b"audio-bytes"),
            filename="sample.wav",
            content_type="audio/wav",
        )

        with (
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(
                app_module.requests,
                "post",
                return_value=FakeResponse(503, {"error": "busy"}),
            ) as post,
            patch.object(app_module.time, "sleep") as sleep,
        ):
            with self.assertRaises(app_module.ServiceError):
                app_module._transcribe_audio_file(audio_file)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_openai_audio_endpoint_returns_one_valid_json_document(self):
        s2t_response = FakeResponse(200, {"text": "原始转录"})
        with (
            patch.object(app_module, "API_ACCESS_TOKEN", "access"),
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(app_module, "OPT_API_KEY", None),
            patch.object(app_module.requests, "post", return_value=s2t_response),
        ):
            response = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b"audio"), "sample.wav"),
                    "model": app_module.MODEL_CALIBRATE,
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        decoded = json.loads(response.get_data(as_text=True))
        self.assertEqual(decoded["text"], "原始转录")
        self.assertEqual(response.content_type.split(";")[0], "application/json")

    def test_openai_audio_endpoint_supports_text_response_format(self):
        with (
            patch.object(app_module, "API_ACCESS_TOKEN", "access"),
            patch.object(app_module, "_transcribe_audio_file", return_value="原始转录"),
            patch.object(
                app_module,
                "_perform_text_optimization",
                return_value=("校准转录", "校准成功", True),
            ),
        ):
            response = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b"audio"), "sample.wav"),
                    "model": app_module.MODEL_CALIBRATE,
                    "response_format": "text",
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type.split(";")[0], "text/plain")
        self.assertEqual(response.get_data(as_text=True), "校准转录")

    def test_openai_audio_endpoint_rejects_unknown_response_format(self):
        with patch.object(app_module, "API_ACCESS_TOKEN", "access"):
            response = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b"audio"), "sample.wav"),
                    "model": app_module.MODEL_CALIBRATE,
                    "response_format": "srt",
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["type"], "invalid_request_error")

    def test_summary_model_uses_raw_text_when_calibration_is_unavailable(self):
        with (
            patch.object(app_module, "API_ACCESS_TOKEN", "access"),
            patch.object(app_module, "_transcribe_audio_file", return_value="原始转录"),
            patch.object(
                app_module,
                "_perform_text_optimization",
                return_value=("原始转录", "校准服务未配置", False),
            ),
            patch.object(
                app_module,
                "_perform_summarization",
                return_value={"status": "success", "summary": "原文摘要"},
            ) as summarize,
        ):
            response = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b"audio"), "sample.wav"),
                    "model": app_module.MODEL_SUMMARIZE,
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["text"], "原文摘要")
        self.assertEqual(
            payload["x_warning"]["code"],
            "calibration_failed_in_summary_workflow",
        )
        summarize.assert_called_once_with("原始转录")

    def test_v1_oversized_request_uses_openai_error_shape(self):
        with (
            patch.object(app_module, "API_ACCESS_TOKEN", "access"),
            patch.dict(app_module.app.config, {"MAX_CONTENT_LENGTH": 16}),
        ):
            response = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b"audio" * 100), "sample.wav"),
                    "model": app_module.MODEL_CALIBRATE,
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error"]["code"], "file_too_large")

    def test_v1_per_file_limit_is_reported_as_client_error(self):
        with (
            patch.object(app_module, "API_ACCESS_TOKEN", "access"),
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(app_module, "MAX_UPLOAD_BYTES", 3),
            patch.object(app_module, "MAX_UPLOAD_MB", 1),
            patch.object(app_module.requests, "post") as post,
        ):
            oversized = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b"four"), "sample.wav"),
                    "model": app_module.MODEL_CALIBRATE,
                },
                content_type="multipart/form-data",
            )
            empty = self.client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": "Bearer access"},
                data={
                    "file": (io.BytesIO(b""), "sample.wav"),
                    "model": app_module.MODEL_CALIBRATE,
                },
                content_type="multipart/form-data",
            )
        post.assert_not_called()
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.get_json()["error"]["type"], "invalid_request_error")
        self.assertEqual(oversized.get_json()["error"]["code"], "file_too_large")
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.get_json()["error"]["type"], "invalid_request_error")
        self.assertEqual(empty.get_json()["error"]["code"], "invalid_file")

    def test_upstream_error_is_reported_as_gateway_error(self):
        with (
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(
                app_module.requests,
                "post",
                return_value=FakeResponse(500, {"error": "service unavailable"}),
            ) as post,
            patch.object(app_module.time, "sleep") as sleep,
        ):
            response = self.client.post(
                "/api/transcribe",
                data={"audio_file": (io.BytesIO(b"audio"), "sample.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("service unavailable", response.get_json()["error"])
        self.assertEqual(post.call_count, app_module.RETRY_ATTEMPTS)
        self.assertEqual(sleep.call_count, app_module.RETRY_ATTEMPTS - 1)

    def test_split_preserves_whitespace_only_text(self):
        for source in (" ", "   ", "\t\n", " " * 40):
            with self.subTest(source=repr(source)):
                chunks = app_module._split_text_intelligently(source, chunk_size=8)
                self.assertEqual("".join(chunks), source)
                self.assertTrue(all(len(chunk) <= 8 for chunk in chunks))
        self.assertEqual(app_module._split_text_intelligently("", chunk_size=8), [])

    def test_v1_auth_rejects_non_ascii_bearer_token_without_server_error(self):
        with patch.object(app_module, "API_ACCESS_TOKEN", "secret"):
            response = self.client.get(
                "/v1/models", headers={"Authorization": "Bearer 密钥"}
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["type"], "invalid_request_error")

    def test_unexpected_api_exception_returns_json_instead_of_html(self):
        with (
            patch.object(app_module, "S2T_API_KEY", "s2t-key"),
            patch.object(app_module, "S2T_MODEL", "s2t-model"),
            patch.object(
                app_module, "S2T_API_URL", "https://s2t.example.test/transcribe"
            ),
            patch.object(
                app_module, "_transcribe_audio_file", side_effect=RuntimeError("boom")
            ),
            patch.dict(
                app_module.app.config,
                {"PROPAGATE_EXCEPTIONS": False, "TESTING": False},
            ),
        ):
            response = self.client.post(
                "/api/transcribe",
                data={"audio_file": (io.BytesIO(b"audio"), "sample.wav")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type.split(";")[0], "application/json")
        self.assertEqual(response.get_json()["error"]["type"], "server_error")

    def test_url_validation_rejects_whitespace_and_malformed_values(self):
        valid = ("https://api.example.com/v1", "http://127.0.0.1:8080/x")
        invalid = (
            "https://api.example.com/a b",
            "http://",
            "ftp://api.example.com",
            None,
            42,
        )
        for value in valid:
            with self.subTest(value=value):
                self.assertTrue(app_module._is_valid_http_url(value))
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(app_module._is_valid_http_url(value))

    def test_web_transcription_status_message_reflects_calibration_outcome(self):
        cases = (
            ("校准成功！", True, "转录完成，校准成功！"),
            (
                "校准已跳过 (校准服务配置不完整: 缺少API Key)",
                False,
                "转录完成（校准已跳过 (校准服务配置不完整: 缺少API Key)）",
            ),
            ("校准失败 (请求超时)", False, "转录完成，但校准失败 (请求超时)"),
        )
        for opt_message, calibrated, expected in cases:
            with self.subTest(opt_message=opt_message):
                with (
                    patch.object(
                        app_module, "_transcribe_audio_file", return_value="原始转录"
                    ),
                    patch.object(
                        app_module,
                        "_perform_text_optimization",
                        return_value=("校准文本", opt_message, calibrated),
                    ),
                ):
                    response = self.client.post(
                        "/api/transcribe",
                        data={"audio_file": (io.BytesIO(b"audio"), "sample.wav")},
                        content_type="multipart/form-data",
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["calibration_message"], expected)


if __name__ == "__main__":
    unittest.main()
