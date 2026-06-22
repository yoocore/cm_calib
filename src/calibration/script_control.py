import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.calibration.calib_types import ParameterSpec
from src.calibration.utils import _unlink_if_exists
from src.health.dde_health_check import render_dde_execute_script


class ScriptControlMixin:
    def preflight_script_control(self) -> None:
        self.script_control_template_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_script_path.parent.mkdir(parents=True, exist_ok=True)
        self.script_control_result_path.parent.mkdir(parents=True, exist_ok=True)
        print(
            "Script Control preflight: "
            f"template_path={self.script_control_template_path}, "
            f"runtime_path={self.script_control_script_path}, "
            f"result_path={self.script_control_result_path}, "
            f"dde_service={self.script_control_dde_service}, "
            f"dde_topic={self.script_control_dde_topic}"
        )

    def _record_dde_operation_success(self) -> None:
        if self._dde_recovery_probe_active:
            return
        self._reset_dde_dispatch_circuit()

    def _record_dde_operation_failure(self, exc: BaseException, operation: str) -> None:
        if self._dde_recovery_probe_active:
            return
        if not self._runtime_error_needs_dde_recovery_probe(exc):
            self._reset_dde_dispatch_circuit()
            return

        self.dde_dispatch_failure_streak += 1
        self.dde_circuit_last_error_text = self._summarize_dde_detail(exc)
        if self.dde_dispatch_failure_streak < self.dde_circuit_trip_failures:
            return
        if self.dde_circuit_opened_at is None:
            self.dde_circuit_opened_at = time.perf_counter()
            self._log_dde_retry_event(
                "dde_dispatch_circuit",
                self.dde_dispatch_failure_streak,
                self.dde_circuit_trip_failures,
                "opened",
                0.0,
                detail=f"operation={operation} error={self.dde_circuit_last_error_text}",
            )

    def _apply_value_map_or_recover(self, values: Dict[str, float], context: str) -> None:
        try:
            self._apply_value_map(values)
        except RuntimeError as exc:
            restored = self._recover_after_runtime_error(values, exc)
            if restored:
                return
            raise RuntimeError(f"{context}: {exc}") from exc

    def _apply_script_control_params(self, params: List[ParameterSpec]) -> None:
        if not params:
            return

        for param in params:
            param.value = self._quantize_param_value(param, param.value)

        last_error: Optional[RuntimeError] = None
        for attempt in range(3):
            msg = self._run_script_control_script(self._render_script_control_apply_script(params))
            observed: Dict[str, float] = {}
            for line in msg.splitlines():
                if "=" not in line:
                    continue
                name, raw_value = line.split("=", 1)
                try:
                    observed[name.strip()] = float(raw_value.replace(",", ".").strip())
                except ValueError:
                    continue

            mismatches: List[str] = []
            for param in params:
                expected = self._quantize_param_value(param, param.value)
                actual = observed.get(param.name)
                read_decimals = self.SCRIPT_CONTROL_READ_DECIMALS.get(param.name, param.decimals)
                if not self._script_control_readback_matches(
                    expected,
                    actual,
                    param.decimals,
                    read_decimals,
                ):
                    expected_readback = self._quantize_value(expected, read_decimals)
                    mismatches.append(
                        f"{param.name}: expected {expected_readback}, read back {actual}"
                    )

            if not mismatches:
                time.sleep(self.script_control_settle_sec)
                return

            last_error = RuntimeError(
                "Script Control verification failed after apply attempt "
                f"{attempt + 1}: " + "; ".join(mismatches)
            )
            time.sleep(0.1)

        if last_error is not None:
            raise last_error

        time.sleep(self.script_control_settle_sec)

    def _preflight_capture_aspect_ratio(self) -> None:
        try:
            raw_w, raw_h = self._get_movie_dde_view_size()
        except RuntimeError as exc:
            print(f"Capture aspect preflight skipped: {exc}")
            return
        ref_h, ref_w = self.real_img.shape[:2]

        if raw_w * ref_h != ref_w * raw_h:
            print(
                "WARNING: Current movie capture aspect ratio does not match real_image: "
                f"captured={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
            )
            return
        print(
            "Capture aspect preflight: "
            f"raw={raw_w}x{raw_h}, real={ref_w}x{ref_h}"
        )

    def _capture_movie_via_dde(self, tag: str) -> Path:
        out_path = self.output_dir / f"{tag}.png"

        try:
            import dde  # type: ignore
        except Exception as exc:
            raise RuntimeError("movie dde capture requires pywin32 DDE support") from exc

        last_runtime_error: Optional[RuntimeError] = None
        attempt_count = 2  # Fast-fail: orchestrator retries with fresh processes
        retry_delay = max(self.script_control_settle_sec, 0.2)
        for attempt in range(attempt_count):
            attempt_no = attempt + 1
            attempt_started = time.perf_counter()
            attempt_runtime_error: Optional[RuntimeError] = None

            invocation_id = uuid.uuid4().hex
            script_path = self.output_dir / f"{tag}_movie_capture_dde.{invocation_id}.tcl"
            result_path = self.output_dir / f"{tag}_movie_capture_dde.{invocation_id}.txt"
            # Use Python-computed FBO dimensions if available (reliable even when window minimized)
            if self._capture_width and self._capture_height:
                _wi = self._capture_width
                _he = self._capture_height
            else:
                # Fallback: read from View() dict
                _wi = '[dict get $View($vno) Width]'
                _he = '                [dict get $View($vno) Height]'
            body_lines = [
                'set vno $View(ev.view)',
                f'set wi {_wi}',
                f'set he {_he}',
                'UpdateView $vno',
                'catch {gl bindframebuffer_read 0}',
                'catch {FBO end}',
                'set captureFBO [FBO new $wi $he -tex rgb -noclear]',
                'set update_rc [catch {',
                '    FBO begin $captureFBO',
                '    UpdateView $vno',
                '    FBO end',
                '} update_msg]',
                'catch {FBO end}',
                'if {$update_rc != 0} {',
                '    catch {FBO delete $captureFBO}',
                '    error $update_msg',
                '}',
                'catch {image delete probeImg}',
                'image create photo probeImg -width $wi -height $he',
                'gl bindframebuffer_read $captureFBO',
                'gl readpixels 0 0 probeImg',
                f'probeImg write "{out_path.as_posix()}" -format png',
                'catch {gl bindframebuffer_read 0}',
                'catch {FBO delete $captureFBO}',
            ]
            script_text = render_dde_execute_script(
                result_path,
                "IPG-MOVIE",
                body_lines,
            )
            script_path.write_text(script_text, encoding="utf-8")
            _unlink_if_exists(result_path)
            self._ensure_dde_dispatch_ready("movie_capture")

            server = None
            try:
                server = dde.CreateServer()
                server.Create(f"CopilotMovieCapture.{uuid.uuid4().hex}")
                conv = dde.CreateConversation(server)
                conv.ConnectTo(self.script_control_dde_service, self.script_control_dde_topic)
                conv.Exec(f"RunScript {{{script_path.as_posix()}}}")
            except Exception as exc:
                attempt_runtime_error = RuntimeError(f"movie dde RunScript failed: {exc}")
            finally:
                if server is not None:
                    try:
                        server.Shutdown()
                    except Exception:
                        pass

            if attempt_runtime_error is None:
                deadline = time.time() + self.script_control_timeout_sec
                while time.time() < deadline:
                    if result_path.exists():
                        text = result_path.read_text(encoding="utf-8", errors="replace")
                        if self._is_script_control_result_complete(text):
                            rc, msg = self._parse_script_control_result_text(text)
                            if rc != 0:
                                attempt_runtime_error = RuntimeError(f"movie dde capture failed: {msg}")
                                break
                            self._log_dde_retry_event(
                                "movie_capture",
                                attempt_no,
                                attempt_count,
                                "success",
                                time.perf_counter() - attempt_started,
                                detail=f"output={out_path.name}",
                            )
                            self._record_dde_operation_success()
                            _unlink_if_exists(script_path)
                            _unlink_if_exists(result_path)
                            return out_path
                    time.sleep(0.05)

            if attempt_runtime_error is None:
                attempt_runtime_error = RuntimeError("Timed out waiting for movie dde capture result")
            last_runtime_error = attempt_runtime_error
            retry_sleep_sec = retry_delay * attempt_no if attempt < attempt_count - 1 else None
            self._log_dde_retry_event(
                "movie_capture",
                attempt_no,
                attempt_count,
                "retry" if retry_sleep_sec is not None else "failed",
                time.perf_counter() - attempt_started,
                detail=attempt_runtime_error,
                retry_sleep_sec=retry_sleep_sec,
            )
            # Keep files for debugging when capture fails
            # _unlink_if_exists(script_path)
            # _unlink_if_exists(result_path)
            # Run CarMaker error diagnostic on last attempt failure
            if attempt_runtime_error is not None:
                self._diagnose_carmaker_after_failure(attempt_runtime_error)
            if retry_sleep_sec is not None:
                if self._runtime_error_needs_dde_recovery_probe(attempt_runtime_error):
                    if self._wait_for_dde_service_recovery():
                        continue
                time.sleep(retry_sleep_sec)

        if last_runtime_error is not None:
            self._record_dde_operation_failure(last_runtime_error, "movie_capture")
            raise last_runtime_error
        final_error = RuntimeError("Timed out waiting for movie dde capture result")
        self._record_dde_operation_failure(final_error, "movie_capture")
        raise final_error

    def capture_movie(self, tag: str) -> Path:
        return self._capture_movie_via_dde(tag)

    def _force_update_view(self) -> None:
        try:
            import dde  # type: ignore
        except ImportError:
            return
        server = dde.CreateServer()
        server.Create("CalibForceUpdate")
        conv = dde.CreateConversation(server)
        conv.ConnectTo(self.movie_apphost, "IPG-MOVIE")
        conv.Exec('set vno $View(ev.view); UpdateView $vno')
        server.Disconnect()
        time.sleep(0.5)

    def _diagnose_carmaker_after_failure(self, error: RuntimeError) -> None:
        """Check CarMaker/IPG-MOVIE error state after a DDE failure.
        Runs a lightweight DDE probe to capture any CarMaker-side errors
        (e.g. FBO Creation error) that aren't visible in Python's DDE output.
        """
        try:
            from src.health.dde_health_check import run_check_attempt, default_output_dir
            from pathlib import Path
            output_dir = default_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            body = [
                'puts stdout "--- CarMaker Error Diagnostic ---"',
                'puts stdout "errorInfo: [set ::errorInfo]"',
                'puts stdout "View array: [array names View]"',
                'if {[info exists View(ev.view)]} { puts stdout "View(ev.view): $View(ev.view)" }',
                'if {[info exists View(0)]} { puts stdout "View(0): $View(0)" }',
                'puts stdout "CheckViewPort: [info commands CheckViewPort]"',
                'puts stdout "CheckViewPort_saved: [info commands CheckViewPort_saved]"',
                'puts stdout "ConfigFBO exists: [info commands ConfigFBO]"',
                'catch { puts stdout "FBO test: [FBO new 100 100 -tex rgb -noclear]" } err_fbo',
                'puts stdout "FBO new error: $err_fbo"',
                'if {[info exists err_fbo] && $err_fbo eq ""} { catch {FBO delete $::__test_fbo} }',
            ]
            result = run_check_attempt(
                name="diag_after_fail",
                service="TclEval", topic="CarMaker",
                output_dir=output_dir,
                script_text="\n".join(body),
                timeout_sec=5,
            )
            if result.get("ok") and result.get("detail"):
                print(f"[carmaker_diag] {result['detail']}")
            else:
                print(f"[carmaker_diag] probe failed: {result.get('detail')}")
        except Exception as exc:
            print(f"[carmaker_diag] error: {exc}")
        # Fallback: try clipboard-based dialog capture when DDE is unresponsive
        try:
            err = self._capture_carmaker_error_dialog()
            if err:
                # Check if it's an error (not just empty/no-error state)
                if "ERROR" in err or "invalid" in err or "FBO" in err or "ConfigFBO" in err:
                    print(f"[carmaker_diag] CAPTURED FROM DEBUGGER: {err}")
        except Exception:
            pass

    def _apply_value_map(self, values: Dict[str, float]) -> None:
        touched = False
        touched_params: List[ParameterSpec] = []
        for param in self.params:
            if param.name not in values:
                continue
            param.value = float(values[param.name])
            touched_params.append(param)
            touched = True
        if touched_params:
            changed_names = [p.name for p in touched_params]
            print(f"Applying {len(touched_params)} params: {changed_names}")
            self._apply_script_control_params(touched_params)
        if touched:
            time.sleep(self.settle_sec)
