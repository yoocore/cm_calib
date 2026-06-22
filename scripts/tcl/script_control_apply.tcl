set __copilot_sc_out [open "C:/CM_Projects/CMO141_Calibration/SimOutput/script_control_camera_apply_result.txt" w]
set rc [catch {send IPG-MOVIE {
    if {![winfo exists .camera]} {error "missing widget .camera"}
    set result {}
    if {![winfo exists .camera.presetFrame.evptz]} {error "missing widget .camera.presetFrame.evptz"}
    lappend result "pos_z=[.camera.presetFrame.evptz get]"
    if {![winfo exists .camera.presetFrame.y]} {error "missing widget .camera.presetFrame.y"}
    lappend result "pitch=[.camera.presetFrame.y get]"
    if {![winfo exists .camera.presetFrame.z]} {error "missing widget .camera.presetFrame.z"}
    lappend result "yaw=[.camera.presetFrame.z get]"
    if {![winfo exists .camera.presetFrame.evptx]} {error "missing widget .camera.presetFrame.evptx"}
    lappend result "pos_x=[.camera.presetFrame.evptx get]"
    if {![winfo exists .camera.presetFrame.x]} {error "missing widget .camera.presetFrame.x"}
    lappend result "roll=[.camera.presetFrame.x get]"
    if {![winfo exists .camera.presetFrame.evpty]} {error "missing widget .camera.presetFrame.evpty"}
    lappend result "pos_y=[.camera.presetFrame.evpty get]"
    if {![winfo exists .camera.cammoddlg.fov.e]} {error "missing widget .camera.cammoddlg.fov.e"}
    lappend result "lens_fov=[.camera.cammoddlg.fov.e get]"
    if {![winfo exists .camera.cammoddlg.fisheye.ctrl.e1]} {error "missing widget .camera.cammoddlg.fisheye.ctrl.e1"}
    lappend result "lens_scale=[.camera.cammoddlg.fisheye.ctrl.e1 get]"
    if {![winfo exists .camera.cammoddlg.fisheye.ctrl.e2]} {error "missing widget .camera.cammoddlg.fisheye.ctrl.e2"}
    lappend result "lens_offset_x=[.camera.cammoddlg.fisheye.ctrl.e2 get]"
    if {![winfo exists .camera.cammoddlg.fisheye.ctrl.e3]} {error "missing widget .camera.cammoddlg.fisheye.ctrl.e3"}
    lappend result "lens_offset_y=[.camera.cammoddlg.fisheye.ctrl.e3 get]"
    join $result "\n"
}} msg]
puts $__copilot_sc_out "rc=$rc"
puts $__copilot_sc_out "msg_begin"
puts $__copilot_sc_out $msg
puts $__copilot_sc_out "msg_end"
close $__copilot_sc_out
