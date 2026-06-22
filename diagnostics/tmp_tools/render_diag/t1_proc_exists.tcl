set __copilot_out_0016c6a1725944ae9a4e5fa70d01890c [open "tmp/render_diag/t1_proc_exists.txt" w]
set __copilot_remote_result_path "tmp/render_diag/t1_proc_exists.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "tmp/render_diag/t1_proc_exists.txt.remote" w]
        set __copilot_remote_rc [catch {
            set proc_exists [info commands UpdateView_TimerProc]
            list PROC_EXISTS $proc_exists
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
    puts -nonewline $__copilot_out_0016c6a1725944ae9a4e5fa70d01890c $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_0016c6a1725944ae9a4e5fa70d01890c "rc=$rc"
    puts $__copilot_out_0016c6a1725944ae9a4e5fa70d01890c "msg_begin"
    puts $__copilot_out_0016c6a1725944ae9a4e5fa70d01890c $msg
    puts $__copilot_out_0016c6a1725944ae9a4e5fa70d01890c "msg_end"
}
close $__copilot_out_0016c6a1725944ae9a4e5fa70d01890c
