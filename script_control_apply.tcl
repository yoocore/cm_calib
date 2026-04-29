set out [open "C:/CM_Projects/CMO141_Calibration/SimOutput/script_control_camera_apply_result.txt" w]
proc emit {text} {
    global out
    puts $out $text
}
set rc [catch {send IPG-MOVIE {
    if {![winfo exists .camera]} {error "missing widget .camera"}
    if {![winfo exists .camera.presetFrame.evptz]} {error "missing widget .camera.presetFrame.evptz"}
    .camera.presetFrame.evptz delete 0 end
    .camera.presetFrame.evptz insert 0 0.6700
    if {![winfo exists .camera.presetFrame.y]} {error "missing widget .camera.presetFrame.y"}
    .camera.presetFrame.y delete 0 end
    .camera.presetFrame.y insert 0 17.5810
    if {![winfo exists .camera.presetFrame.z]} {error "missing widget .camera.presetFrame.z"}
    .camera.presetFrame.z delete 0 end
    .camera.presetFrame.z insert 0 180.1789
    if {![winfo exists .camera.presetFrame.evptx]} {error "missing widget .camera.presetFrame.evptx"}
    .camera.presetFrame.evptx delete 0 end
    .camera.presetFrame.evptx insert 0 0.2880
    if {![winfo exists .camera.presetFrame.x]} {error "missing widget .camera.presetFrame.x"}
    .camera.presetFrame.x delete 0 end
    .camera.presetFrame.x insert 0 -0.0200
    if {![winfo exists .camera.presetFrame.evpty]} {error "missing widget .camera.presetFrame.evpty"}
    .camera.presetFrame.evpty delete 0 end
    .camera.presetFrame.evpty insert 0 0.0400
    if {![winfo exists .camera.cammoddlg.fov.e]} {error "missing widget .camera.cammoddlg.fov.e"}
    .camera.cammoddlg.fov.e delete 0 end
    .camera.cammoddlg.fov.e insert 0 195.8
    if {![winfo exists .camera.cammoddlg.fisheye.ctrl.e1]} {error "missing widget .camera.cammoddlg.fisheye.ctrl.e1"}
    .camera.cammoddlg.fisheye.ctrl.e1 delete 0 end
    .camera.cammoddlg.fisheye.ctrl.e1 insert 0 1.000
    if {![winfo exists .camera.cammoddlg.fisheye.ctrl.e2]} {error "missing widget .camera.cammoddlg.fisheye.ctrl.e2"}
    .camera.cammoddlg.fisheye.ctrl.e2 delete 0 end
    .camera.cammoddlg.fisheye.ctrl.e2 insert 0 0.00
    if {![winfo exists .camera.cammoddlg.fisheye.ctrl.e3]} {error "missing widget .camera.cammoddlg.fisheye.ctrl.e3"}
    .camera.cammoddlg.fisheye.ctrl.e3 delete 0 end
    .camera.cammoddlg.fisheye.ctrl.e3 insert 0 0.00
    update idletasks
    if {![winfo exists .camera.btn.set]} {error "missing widget .camera.btn.set"}
    .camera.btn.set invoke
    update idletasks
    set result {}
    if {![winfo exists .camera.presetFrame.svptz]} {error "missing widget .camera.presetFrame.svptz"}
    lappend result "pos_z=[.camera.presetFrame.svptz get]"
    if {![winfo exists .camera.presetFrame.y]} {error "missing widget .camera.presetFrame.y"}
    lappend result "pitch=[.camera.presetFrame.y get]"
    if {![winfo exists .camera.presetFrame.z]} {error "missing widget .camera.presetFrame.z"}
    lappend result "yaw=[.camera.presetFrame.z get]"
    if {![winfo exists .camera.presetFrame.svptx]} {error "missing widget .camera.presetFrame.svptx"}
    lappend result "pos_x=[.camera.presetFrame.svptx get]"
    if {![winfo exists .camera.presetFrame.x]} {error "missing widget .camera.presetFrame.x"}
    lappend result "roll=[.camera.presetFrame.x get]"
    if {![winfo exists .camera.presetFrame.svpty]} {error "missing widget .camera.presetFrame.svpty"}
    lappend result "pos_y=[.camera.presetFrame.svpty get]"
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
emit "rc=$rc"
emit "msg_begin"
emit $msg
emit "msg_end"
close $out
