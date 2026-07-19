from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO / relative_path).read_text()


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start:index + 1]
    raise AssertionError(f"Could not find body for {signature}")


class AsyncSharePipelineContractTest(unittest.TestCase):
    def test_uart_result_task_transfers_job_ownership_without_network_io(self):
        source = _read("main/tasks/asic_result_task.cpp")
        result_task = _function_body(source, "void ASIC_result_task")

        self.assertNotIn("submitShare(", result_task)
        self.assertIn("QueuedShare share{job", result_task)
        self.assertIn("if (enqueueShare(share))", result_task)
        self.assertIn("job = nullptr;", result_task)
        self.assertIn("if (job)", result_task)
        self.assertIn("free_bm_job(job);", result_task)

    def test_worker_startup_is_per_pool_and_failed_queue_is_released(self):
        source = _read("main/tasks/asic_result_task.cpp")
        startup = _function_body(source, "static size_t startShareWorkers")
        enqueue = _function_body(source, "static bool enqueueShare")

        self.assertIn("worker.ready = false", startup)
        self.assertIn("&worker.task", startup)
        self.assertIn("8192, &worker", startup)
        self.assertIn("vQueueDelete(queue)", startup)
        self.assertNotIn("return false;", startup)
        self.assertIn("!worker.ready || !worker.queue || !worker.task", enqueue)
        self.assertIn("incrementMetric(shareQueueDrops)", enqueue)

    def test_queue_age_metrics_and_duplicate_side_effects_are_guarded(self):
        source = _read("main/tasks/asic_result_task.cpp")
        worker = _function_body(source, "static void shareSubmitTask")
        key = _function_body(source, "static uint64_t make_key")
        result_task = _function_body(source, "void ASIC_result_task")

        self.assertIn("SHARE_MAX_QUEUE_AGE_MS", worker)
        self.assertIn("incrementMetric(shareQueueDrops)", worker)
        self.assertEqual(1, worker.count("free_bm_job(share.job)"))
        self.assertIn("prev_block_hash", key)
        self.assertIn("merkle_root", key)
        self.assertIn("job->ntime", key)
        self.assertNotIn("strlen", key)
        nonduplicate = result_task.index("if (!duplicate) {")
        best = result_task.index("checkForBestDiff")
        found = result_task.index("checkForFoundBlock")
        enqueue = result_task.index("if (!duplicate && nonce_diff >= job->pool_diff)")
        self.assertLess(nonduplicate, best)
        self.assertLess(best, found)
        self.assertLess(found, enqueue)

        increment = _function_body(source, "static void incrementMetric")
        read = _function_body(source, "static uint64_t readMetric")
        for body in (increment, read):
            self.assertIn("portENTER_CRITICAL(&s_metricsMux)", body)
            self.assertIn("portEXIT_CRITICAL(&s_metricsMux)", body)

    def test_job_wakes_are_latched_and_coalesced(self):
        source = _read("main/tasks/create_jobs_task.cpp")
        timer = _function_body(source, "static void create_job_timer")
        trigger = _function_body(source, "void trigger_job_creation")
        task = _function_body(source, "void create_jobs_task")

        self.assertIn("static bool job_wake_pending = false", source)
        self.assertNotIn("job_wake_count", source)
        for body in (timer, trigger):
            self.assertIn("job_wake_pending = true", body)
            self.assertIn("pthread_cond_signal(&job_cond)", body)
        self.assertIn("while (!job_wake_pending)", task)
        self.assertIn("job_wake_pending = false", task)


if __name__ == "__main__":
    unittest.main()
