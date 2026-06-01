set __co_eb9cc501 [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.txt" w]
set __remote "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.remote"
catch {file delete -force $__remote}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __rout [open "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.remote" w]
        set __rrc [catch {
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
            probeImg write "C:/CM_Projects/CMO141_Calibration/Data/Script/CameraCalibration/tmp/view_check/fbo_capture.png" -format png
            catch {gl bindframebuffer_read 0}
            catch {FBO delete $captureFBO}
            format "captured=%dx%d" $wi $he
        } __rmsg]
        puts $__rout "rc=$__rrc"
        puts $__rout "msg_begin"
        puts $__rout $__rmsg
        puts $__rout "msg_end"
        close $__rout
        if {$__rrc != 0} {error $__rmsg}
    }
} msg]
if {[file exists $__remote]} {
    set __rin [open $__remote r]
    set __rp [read $__rin]
    close $__rin
    puts -nonewline $__co_eb9cc501 $__rp
    catch {file delete -force $__remote}
} else {
    puts $__co_eb9cc501 "rc=$rc"
    puts $__co_eb9cc501 "msg_begin"
    puts $__co_eb9cc501 $msg
    puts $__co_eb9cc501 "msg_end"
}
close $__co_eb9cc501