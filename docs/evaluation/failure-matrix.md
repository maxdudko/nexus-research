# Failure matrix

These rows are the expected outcomes for FR-06. The tests inject failures at existing boundaries. They do not prove recovery after a process crash. A cancel whose status write times out can leave a `running` row. Nothing resumes that row automatically.

Prompt checks reject unknown citation ids and URLs outside the retrieved snapshot. A section that cites a real id may still repeat attacker prose. That is a limit of the report boundary, not evidence that the passage entails the sentence.

| Failure | Expected outcome | Test |
| --- | --- | --- |
| OpenAI timeout | `provider_timeout`; provider text is omitted; a synthesis timeout does not publish `completed` | `test_timeout_does_not_reveal_the_provider_text`, `test_provider_timeout_does_not_publish_a_completed_report`, `test_timeout_before_metadata_stays_unavailable` |
| OpenAI 429 | `provider_rate_limited` | `test_rate_limit_uses_429` |
| OpenAI refusal | `model_refused`; refusal text is omitted | `test_refusal_does_not_reveal_the_refusal_text` |
| Malformed model output | `invalid_model_output`; synthesis retries once, then a partial report with the real sources | `test_empty_output_is_invalid`, `test_a_second_malformed_report_falls_back_without_invented_sources` |
| OpenAI unavailable | `provider_unavailable` | `map_provider_exception` default branch; `test_failed_upsert_does_not_hide_the_provider_error` for Qdrant |
| SDK retries unknown | `physical_attempts` is null unless `ai_max_retries` is 0; coverage stays `partial` | `test_sdk_retries_keep_observed_chat_partial`, `test_retries_disabled_records_one_measured_attempt` |
| Missing token usage | tokens null, `usage_source` `unavailable` | `test_zero_token_pair_is_not_a_measurement`, `test_missing_structured_usage_stays_unavailable` |
| Tavily timeout then success | one logical `search_calls`; one `web_search` observation per HTTP attempt with `physical_attempts` 1 | `test_tavily_retry_keeps_one_logical_search`, `test_timeout_is_retried_once` |
| Tavily 429 | not retried; discovery aborts | `test_rate_limit_is_not_retried`, `test_rate_limit_aborts_the_request` |
| Tavily malformed payload | `search_failed` | `test_malformed_payload_is_search_failed` |
| Qdrant unavailable | `provider_unavailable`; no invented matches | `test_failed_upsert_does_not_hide_the_provider_error`, `test_store_failure_propagates`, `test_embedding_failure_does_not_search` |
| Cross-scope evidence | another research's text is not cited | `test_another_research_version_is_not_requested`, `test_duplicate_ingest_stays_one_source_and_foreign_text_is_not_cited` |
| Report insert failure | no `research.completed`; stored status `failed`, code `history_unavailable` | `test_report_insert_failure_stores_failed_without_completed` |
| Commit failure | no `research.completed` | `test_commit_failure_does_not_emit_completed`, `test_report_write_failure_rolls_back_the_status` |
| History failure before headers | the stream permit is released | `test_history_failure_before_headers_releases_the_permit` |
| Checkpoint after cancel | the thread can still be read; status stays interrupted or `running` when the status write times out | `test_cancellation_leaves_a_running_checkpoint`, `test_disconnect_leaves_running_when_the_status_write_times_out` |
| SSE disconnect | the graph is cancelled and the run is marked interrupted | `test_asgi_disconnect_cancels_the_graph_and_marks_interrupted` |
| SSE after a committed report | exactly one terminal `research.completed` | `test_stream_emits_one_terminal_event_after_the_commit` |
| Heartbeat | a slow node emits a ping before the next progress event | `test_slow_node_emits_a_heartbeat_before_progress` |
| Stream capacity | a second stream is rejected while one holds the permit | `test_a_second_stream_is_rejected_while_one_is_running` |
| Graph caps | defaults are 2 iterations, 6 searches, 13 logical LLM calls, 5 pages, 12 passages, 180 seconds | `test_default_caps_cover_two_discovery_rounds` |
| Deadline | an injected clock interrupts without a second graph invocation | `test_deadline_interrupts_without_a_second_invocation` |
| Private, loopback, link-local, and metadata URLs | `unsafe_url`; no connection | `test_unsafe_urls_do_not_connect` |
| Redirect to a private or loopback address | `unsafe_url` before a second connection | `test_redirect_to_a_private_address_stops`, `test_redirect_to_loopback_stops_before_a_second_connection` |
| DNS rebinding | only the first public address is connected | `test_rebinding_connects_only_to_the_first_public_address`, `test_private_dns_answer_does_not_connect` |
| Oversized web body | `payload_too_large` | `test_missing_length_and_oversized_bodies_are_capped`, `test_declared_length_above_the_cap_is_rejected` |
| PDF over 10 MiB or 100 pages | `payload_too_large` | `test_read_stops_past_the_byte_cap`, `test_page_cap_is_rejected_before_a_later_page` |
| Malformed, encrypted, or blank PDF | bounded rejection; a blank page yields no text | `test_invalid_pdf_bytes_are_rejected`, `test_encrypted_pdf_is_rejected`, `test_blank_pdf_has_no_text` |
| Fabricated citation or foreign URL | `invalid_model_output`; the public message omits the payload | `test_fabricated_citation_and_foreign_url_are_rejected` |
| Injection copied into a real citation | the snapshot sources stay; the prose can remain in the section | `test_cited_attacker_prose_keeps_the_snapshot_sources` |
| Retrieved instruction through the compiled graph | a foreign URL or fabricated id publishes the retrieved snapshot as `partial`; discovery and ingestion are not called again. Instruction wording that cites the real `E1` and contains no foreign URL is accepted, and that wording can remain in the section. Passages are not dropped because they contain instructions | `test_retrieved_instruction_cannot_change_sources_or_tools` |
| Injection text in logs | claim, secret, and foreign URL are absent from the log stream | `test_injection_text_is_not_logged`, `test_prompt_injection_cannot_change_the_bibliography_or_status`, `test_snippet_instructions_do_not_execute_another_tool` |
