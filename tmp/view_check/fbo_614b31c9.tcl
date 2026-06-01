set out [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_614b31c9.txt" w]
set rc [catch {
    package require dde
    dde execute TclEval CarMaker {
        set out2 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_614b31c9.txt.remote" w]
        set rc2 [catch {
            scan $View(ev.view) %d vno
            set wi [dict get $View($vno) Width]
            set he [dict get $View($vno) Height]
            set captureFBO [FBO new $wi $he -tex rgb -noclear]
            set update_rc [catch {
                FBO begin $captureFBO
                UpdateView $vno
                FBO end
            } update_msg]
            catch {FBO end}
            if {$update_rc != 0} {
                catch {FBO delete $captureFBO}
                error $update_msg
            }
            catch {image delete probeImg}
            image create photo probeImg -width $wi -height $he
            gl bindframebuffer_read $captureFBO
            gl readpixels 0 0 probeImg
            probeImg write "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_check.png" -format png
            catch {gl bindframebuffer_read 0}
            catch {FBO delete $captureFBO}
            format "captured=%dx%d" $wi $he
        } msg2]
        puts $out2 "rc=$rc2"
        puts $out2 "msg_begin"
        puts $out2 $msg2
        puts $out2 "msg_end"
        close $out2
    }
} msg]
puts $out "rc=$rc"
puts $out "msg_begin"
puts $out $msg
puts $out "msg_end"
close $out
