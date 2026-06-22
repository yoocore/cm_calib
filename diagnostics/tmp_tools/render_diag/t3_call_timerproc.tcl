set __copilot_out_a5c6532a2bed41239cd7b865bd31f90b [open "tmp/render_diag/t3_call_timerproc.txt" w]
set __copilot_remote_result_path "tmp/render_diag/t3_call_timerproc.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "tmp/render_diag/t3_call_timerproc.txt.remote" w]
        set __copilot_remote_rc [catch {
            set uc_before $::View(UpdateCounter)
            set rc [catch {UpdateView_TimerProc} msg]
            set uc_after $::View(UpdateCounter)
            list RC $rc MSG $msg UC_BEFORE $uc_before UC_AFTER $uc_after
        } __copilot_remote_msg]
        puts $__copilot_remote_out "rc=$__copilot_remote_rc"
        puts $__copilot_remote_out "msg_begin"
        puts $__copilot_remote_out $__copilot_remote_msg
        puts $__copilot_remote_out "msg_end"
        close $__copilot_remote_out
        if {$__copilot_remote_rc != 0} {error $__copilot_remote_msg}
    }
} msg]
set __copilot_remote_wait_deadline [expr {[clock milliseconds] + 1000}]
while {![file exists $__copilot_remote_result_path] && [clock milliseconds] < $__copilot_remote_wait_deadline} {
    after 25
}
if {[file exists $__copilot_remote_result_path]} {
    set __copilot_remote_in [open $__copilot_remote_result_path r]
    set __copilot_remote_payload [read $__copilot_remote_in]
    close $__copilot_remote_in
    puts -nonewline $__copilot_out_a5c6532a2bed41239cd7b865bd31f90b $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_a5c6532a2bed41239cd7b865bd31f90b "rc=$rc"
    puts $__copilot_out_a5c6532a2bed41239cd7b865bd31f90b "msg_begin"
    puts $__copilot_out_a5c6532a2bed41239cd7b865bd31f90b $msg
    puts $__copilot_out_a5c6532a2bed41239cd7b865bd31f90b "msg_end"
}
close $__copilot_out_a5c6532a2bed41239cd7b865bd31f90b
