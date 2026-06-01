set __copilot_out_e50bcd9347f243d8bb9e70835e7c58a4 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.txt" w]
set __copilot_remote_result_path "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.txt.remote" w]
        set __copilot_remote_rc [catch {
            scan $View(ev.view) %d vno
            set wi [dict get $View($vno) Width]
            set he [dict get $View($vno) Height]
            set captureFBO [FBO new $wi $he -tex rgb -noclear]
            set rc [catch {
                FBO begin $captureFBO
                UpdateView $vno
                FBO end
            } emsg]
            catch {FBO end}
            if {$rc != 0} {
                catch {FBO delete $captureFBO}
                error $emsg
            }
            catch {image delete probeImg}
            image create photo probeImg -width $wi -height $he
            gl bindframebuffer_read $captureFBO
            gl readpixels 0 0 probeImg
            probeImg write "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_check.png" -format png
            catch {gl bindframebuffer_read 0}
            catch {FBO delete $captureFBO}
            format "captured=%dx%d" $wi $he
        } __copilot_remote_msg]
        puts $__copilot_remote_out "rc=$__copilot_remote_rc"
        puts $__copilot_remote_out "msg_begin"
        puts $__copilot_remote_out $__copilot_remote_msg
        puts $__copilot_remote_out "msg_end"
        close $__copilot_remote_out
        if {$__copilot_remote_rc != 0} {error $__copilot_remote_msg}
    }
} msg]
if {[file exists $__copilot_remote_result_path]} {
    set __copilot_remote_in [open $__copilot_remote_result_path r]
    set __copilot_remote_payload [read $__copilot_remote_in]
    close $__copilot_remote_in
    puts -nonewline $__copilot_out_e50bcd9347f243d8bb9e70835e7c58a4 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_e50bcd9347f243d8bb9e70835e7c58a4 "rc=$rc"
    puts $__copilot_out_e50bcd9347f243d8bb9e70835e7c58a4 "msg_begin"
    puts $__copilot_out_e50bcd9347f243d8bb9e70835e7c58a4 $msg
    puts $__copilot_out_e50bcd9347f243d8bb9e70835e7c58a4 "msg_end"
}
close $__copilot_out_e50bcd9347f243d8bb9e70835e7c58a4
