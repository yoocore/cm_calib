set __copilot_out_e6c2706cdb414c44b3dab18c1c2defb3 [open "test_dde_conn.txt" w]
set __copilot_remote_result_path "test_dde_conn.txt.remote"
catch {file delete -force $__copilot_remote_result_path}
set rc [catch {
    package require dde
    dde execute TclEval IPG-MOVIE {
        set __copilot_remote_out [open "test_dde_conn.txt.remote" w]
        set __copilot_remote_rc [catch {
            puts ok
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
    puts -nonewline $__copilot_out_e6c2706cdb414c44b3dab18c1c2defb3 $__copilot_remote_payload
    catch {file delete -force $__copilot_remote_result_path}
} else {
    puts $__copilot_out_e6c2706cdb414c44b3dab18c1c2defb3 "rc=$rc"
    puts $__copilot_out_e6c2706cdb414c44b3dab18c1c2defb3 "msg_begin"
    puts $__copilot_out_e6c2706cdb414c44b3dab18c1c2defb3 $msg
    puts $__copilot_out_e6c2706cdb414c44b3dab18c1c2defb3 "msg_end"
}
close $__copilot_out_e6c2706cdb414c44b3dab18c1c2defb3
