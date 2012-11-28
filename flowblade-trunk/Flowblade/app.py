"""
    Flowblade Movie Editor is a nonlinear video editor.
    Copyright 2012 Janne Liljeblad.

    This file is part of Flowblade Movie Editor <http://code.google.com/p/flowblade>.

    Flowblade Movie Editor is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Flowblade Movie Editor is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Flowblade Movie Editor.  If not, see <http://www.gnu.org/licenses/>.
"""

"""
Application module.

Handles application initialization, shutdown, opening projects and changing
sequences.
"""
import glib
import gobject
import gtk
import mlt
import multiprocessing
import os
import time
import threading

import appconsts
import audiomonitoring
import audiowaveform
import clipeffectseditor
import cliprenderer
import compositeeditor
import dialogs
import dnd
import edit
import editevent
import editorpersistance
import editorstate
import editorwindow
import gui
import guicomponents
import keyevents
import keyframeeditor
import mlt
import mltenv
import mltfilters
import mltplayer
import mltprofiles
import mlttransitions
import monitorevent
import movemodes
import persistance
import projectdata
import propertyedit
import render
import respaths
import resync
import sequence
import test
import tlinewidgets
import translations
import trimmodes
import undo
import updater
import useraction
import utils

AUTOSAVE_DIR = "autosave/"
AUTOSAVE_FILE = "autosave/autosave"
autosave_timeout_id = -1
recovery_dialog_id = -1

splash_screen = None
splash_timeout_id = -1
too_small_timeout_id = -1

def main(root_path):
    """
    Called at application start.
    Initializes application with default project.
    """
    # Set paths.
    respaths.set_paths(root_path)

    print "GTK+ version:", gtk.gtk_version
    editorstate.gtk_version = gtk.gtk_version
    editorstate.mlt_version = mlt.LIBMLT_VERSION

    # Create hidden folders if not present
    user_dir = utils.get_hidden_user_dir_path()
    if not os.path.exists(user_dir):
        os.mkdir(user_dir)
    if not os.path.exists(user_dir + mltprofiles.USER_PROFILES_DIR):
        os.mkdir(user_dir + mltprofiles.USER_PROFILES_DIR)
    if not os.path.exists(user_dir + AUTOSAVE_DIR):
        os.mkdir(user_dir + AUTOSAVE_DIR)

    # Init translations module with translations data
    translations.init_languages()
    translations.load_filters_translations()
    mlttransitions.init_module()

    # Load editor prefs and list of recent projects
    editorpersistance.load()

    # Init gtk threads
    gtk.gdk.threads_init()

    # Adjust gui parameters for smaller screens
    scr_w = gtk.gdk.screen_width()
    scr_h = gtk.gdk.screen_height()
    editorstate.SCREEN_HEIGHT = scr_h
    _set_draw_params(scr_w, scr_h)

    # Refuse to run on too small screen.
    if scr_w < 1151 or scr_h < 767:
        _too_small_screen_exit()
        return

    # Splash screen
    if editorpersistance.prefs.display_splash_screen == True: 
        show_splash_screen()

    # Init MLT framework
    repo = mlt.Factory().init()
    
    # Check for codecs and formats on the system
    mltenv.check_available_features(repo)
    render.load_render_profiles()

    # Load filter and compositor descriptions from xml files.
    mltfilters.load_filters_xml(mltenv.services)
    mlttransitions.load_compositors_xml(mltenv.transitions)

    # Create list of available mlt profiles
    mltprofiles.load_profile_list()
    
    # There is always a project open so at startup we create a default project.
    # Set default project as the project being edited.
    editorstate.project = projectdata.get_default_project()

    # Create player object
    create_player()

    # Create main window and set widget handles in gui.py for more convenient reference.
    create_gui()

    # Inits widgets with project data
    init_project_gui()

    # Inits widgets with current sequence data
    init_sequence_gui()

    # Launch player now that data and gui exist
    launch_player()

    # Editor and modules need some more initializing
    init_editor_state()

    # Tracks need to be recentered if window is resized.
    # Connect listener for this now that the tline panel size allocation is sure to be available.
    gui.editor_window.window.connect("size-allocate", lambda w, e:updater.window_resized())
    gui.editor_window.window.connect("window-state-event", lambda w, e:updater.window_resized())

    # show splash
    if ((editorpersistance.prefs.display_splash_screen == True) and
        (not os.path.exists(user_dir + AUTOSAVE_FILE))):
        global splash_timeout_id
        splash_timeout_id = gobject.timeout_add(2600, destroy_splash_screen)
        splash_screen.show_all()

    # Existance of autosaved file hints that program was exited abnormally
    if os.path.exists(user_dir + AUTOSAVE_FILE):
        gobject.timeout_add(10, autosave_recovery_dialog)
    else:
        start_autosave()

    #audiomonitoring.add_audio_level_filter(editorstate.current_sequence().tractor, editorstate.current_sequence().profile)
    #audiomonitoring.start_monitoring()
    
    # Launch gtk+ main loop
    gtk.main()

# ---------------------------------- program, sequence and preoject init
def create_gui():
    """
    Called at app start to create gui objects and handles for them.
    """
    tlinewidgets.load_icons()

    updater.set_default_edit_mode_callback = editevent.set_default_edit_mode
    updater.load_icons()

    # Create window and all child components
    editor_window = editorwindow.EditorWindow()
    
    # Make references to various gui components available via gui module
    gui.capture_references(editor_window)

    # Connect window global key listener
    gui.editor_window.window.connect("key-press-event", keyevents.key_down)
    
    # Give undo a reference to uimanager for menuitem state changes
    undo.set_menu_items(gui.editor_window.uimanager)
    
    # Set button to display sequence in toggled state.
    gui.sequence_editor_b.set_active(True)

def create_player():
    """
    Creates mlt player object
    """
    # Create player and make available from editorstate module.
    editorstate.player = mltplayer.Player(editorstate.project.profile)
    editorstate.player.set_tracktor_producer(editorstate.current_sequence().tractor)

def launch_player():
    # Create SDL output consumer
    editorstate.player.set_sdl_xwindow(gui.tline_display)
    editorstate.player.create_sdl_consumer()

    # Display current sequence tractor
    updater.display_sequence_in_monitor()
    
    # Connect buttons to player methods
    gui.editor_window.connect_player(editorstate.player)
    
    # Start player.
    editorstate.player.start()

def init_project_gui():
    """
    Called after project load to initialize interface
    """
    # Display media files
    gui.media_list_view.fill_data_model()
    try: # Fails if current bin is empty
        selection = gui.media_list_view.treeview.get_selection()
        selection.select_path("0")
    except Exception:
        pass
        
    # Display bins
    gui.bin_list_view.fill_data_model()
    selection = gui.bin_list_view.treeview.get_selection()
    selection.select_path("0")
    
    # Display sequences
    gui.sequence_list_view.fill_data_model()
    selection = gui.sequence_list_view.treeview.get_selection()
    selected_index = editorstate.project.sequences.index(editorstate.current_sequence())
    selection.select_path(str(selected_index))

    render.set_default_values_for_widgets()

def init_sequence_gui():
    """
    Called after project load or changing current sequence 
    to initialize interface.
    """
    # A media file always needs to be selected to make pop-ups work
    # to user expectations
    selection = gui.media_list_view.treeview.get_selection()
    selection.select_path("0")

    # Set initial timeline scale draw params
    editorstate.current_sequence().update_length()
    updater.update_pix_per_frame_full_view()
    updater.init_tline_scale()
    updater.repaint_tline()

def init_editor_state():
    """
    Called after project load or changing current sequence 
    to initalize editor state.
    """
    render.fill_out_profile_widgets()
    
    # Display project data in 'Project' panel
    updater.update_project_info(editorstate.project)

    # Set initial edit mode and set initial gui state
    gui.mode_buttons[editorstate.INSERT_MOVE].set_active(True)
    
    gui.clip_editor_b.set_sensitive(False)
    gui.editor_window.window.set_title(editorstate.project.name + " - Flowblade")
    updater.set_stopped_configuration()
    gui.editor_window.uimanager.get_widget("/MenuBar/FileMenu/Save").set_sensitive(False)
    gui.editor_window.uimanager.get_widget("/MenuBar/EditMenu/Undo").set_sensitive(False)
    gui.editor_window.uimanager.get_widget("/MenuBar/EditMenu/Redo").set_sensitive(False)
    
    # Center tracks vertical display and init some listeners to
    # new value and repaint tracks column.
    tlinewidgets.set_ref_line_y(gui.tline_canvas.widget.allocation)
    gui.tline_column.init_listeners()
    gui.tline_column.widget.queue_draw()

    # Clear editors 
    clipeffectseditor.clear_clip()
    compositeeditor.clear_compositor()
    
    # Show first pages on notebooks
    gui.middle_notebook.set_current_page(0)
    
    # Clear clip selection.
    movemodes.clear_selection_values()

    # Create array needed to update compositors after all edits
    editorstate.current_sequence().restack_compositors()

    # Enable edit action GUI updates
    # These are turned off initially so we can build test projects 
    # with code by using edit.py module which causes gui updates to happen.
    edit.do_gui_update = True
    
def new_project(profile_index, v_tracks, a_tracks):
    sequence.VIDEO_TRACKS_COUNT = v_tracks
    sequence.AUDIO_TRACKS_COUNT = a_tracks
    profile = mltprofiles.get_profile_for_index(profile_index)
    new_project = projectdata.Project(profile)
    open_project(new_project)
        
def open_project(new_project):
    stop_autosave()
    editorstate.project = new_project

    # Inits widgets with project data
    init_project_gui()
    
    # Inits widgets with current sequence data
    init_sequence_gui()

    # Set and display current sequence tractor
    display_current_sequence()
    
    # Editor and modules need some more initializing
    init_editor_state()
    
    # For save time message on close
    useraction.save_time = None
    start_autosave()

def change_current_sequence(index):
    stop_autosave()
    editorstate.project.c_seq = editorstate.project.sequences[index]

    # Inits widgets with current sequence data
    init_sequence_gui()
    
    # update resync data
    resync.sequence_changed(editorstate.project.c_seq)

    # Set and display current sequence tractor
    display_current_sequence()
    
    # Editor and modules needs to do some initializing
    init_editor_state()

    # Display current sequence selected in gui.
    gui.sequence_list_view.fill_data_model()
    selection = gui.sequence_list_view.treeview.get_selection()
    selected_index = editorstate.project.sequences.index(editorstate.current_sequence())
    selection.select_path(str(selected_index))
    start_autosave()

def display_current_sequence():
    # Get shorter alias.
    player = editorstate.player

    player.consumer.stop()
    player.init_for_profile(editorstate.project.profile)
    player.create_sdl_consumer()
    player.set_tracktor_producer(editorstate.current_sequence().tractor)
    player.connect_and_start()
    updater.display_sequence_in_monitor()
    player.seek_frame(0)
    updater.repaint_tline()

# ------------------------------------------------- autosave
def autosave_recovery_dialog():
    dialogs.autosave_recovery_dialog(autosave_dialog_callback, gui.editor_window.window)
    return False

def autosave_dialog_callback(dialog, response):
    dialog.destroy()
    if response == gtk.RESPONSE_OK:
        useraction.actually_load_project(utils.get_hidden_user_dir_path() + AUTOSAVE_FILE, True)
        
def start_autosave():
    global autosave_timeout_id
    time_min = 1 # hard coded, there's code to make configurable later when project wizard etc. is added
    autosave_delay_millis = time_min * 60 * 1000

    print "autosave started"
    autosave_timeout_id = gobject.timeout_add(autosave_delay_millis, do_autosave)
    autosave_file = utils.get_hidden_user_dir_path() + AUTOSAVE_FILE
    persistance.save_project(editorstate.PROJECT(), autosave_file)

def stop_autosave():
    global autosave_timeout_id
    if autosave_timeout_id == -1:
        return
    gobject.source_remove(autosave_timeout_id)
    autosave_timeout_id = -1

def do_autosave():
    autosave_file = utils.get_hidden_user_dir_path() + AUTOSAVE_FILE
    persistance.save_project(editorstate.PROJECT(), autosave_file)
    return True

# ------------------------------------------------- splash screen
def show_splash_screen():
    global splash_screen
    splash_screen = gtk.Window(gtk.WINDOW_TOPLEVEL)
    splash_screen.set_border_width(0)
    splash_screen.set_decorated(False)
    splash_screen.set_position(gtk.WIN_POS_CENTER)
    splash_screen.set_resizable(False)
    img = gtk.image_new_from_file(respaths.IMAGE_PATH + "flowblade_splash_black_small.png")
    splash_screen.add(img)
    splash_screen.set_keep_above(True)
    while(gtk.events_pending()):
        gtk.main_iteration()

def destroy_splash_screen():
    splash_screen.destroy()
    gobject.source_remove(splash_timeout_id)


# ------------------------------------------------------- small screens
def _set_draw_params(scr_w, scr_h):
    if scr_w < 1220:
        editorwindow.NOTEBOOK_WIDTH = 580
        editorwindow.MONITOR_AREA_WIDTH = 500
    if scr_h < 960:
        editorwindow.TOP_ROW_HEIGHT = 460
    if scr_h < 863:
        editorwindow.TOP_ROW_HEIGHT = 420
        sequence.TRACK_HEIGHT_SMALL = appconsts.TRACK_HEIGHT_SMALLEST
        tlinewidgets.HEIGHT = 184
        tlinewidgets.TEXT_Y_SMALL = 15
        tlinewidgets.ID_PAD_Y_SMALL = 2
        tlinewidgets.COMPOSITOR_HEIGHT_OFF = 7
        tlinewidgets.COMPOSITOR_HEIGHT = 14
        tlinewidgets.COMPOSITOR_TEXT_Y = 11
        tlinewidgets.INSRT_ICON_POS_SMALL = (81, 4)
        audiowaveform.SMALL_TRACK_DRAW_CONSTS = (60, 16, 5)

def _too_small_screen_exit():
    global too_small_timeout_id
    too_small_timeout_id = gobject.timeout_add(200, _show_too_small_info)
    # Launch gtk+ main loop
    gtk.main()

def _show_too_small_info():
    gobject.source_remove(too_small_timeout_id)
    primary_txt = _("Too small screen for this application.")
    scr_w = gtk.gdk.screen_width()
    scr_h = gtk.gdk.screen_height()
    secondary_txt = _("Minimum screen dimensions for this application are 1152 x 768.\n") + \
                    _("Your screen dimensions are ") + str(scr_w) + " x " + str(scr_h) + "."
    dialogs.warning_message_with_callback(primary_txt, secondary_txt, None, False, _exit_too_small)

def _exit_too_small(dialog, response):
    dialog.destroy()
    # Exit gtk main loop.
    gtk.main_quit() 
    
# ------------------------------------------------------ shutdown
def shutdown():
    dialogs.exit_confirm_dialog(_shutdown_dialog_callback, get_save_time_msg(), gui.editor_window.window, editorstate.PROJECT().name)
    return True # Signal that event is handled, otherwise it'll destroy window anyway


def get_save_time_msg():
    if useraction.save_time == None:
        return _("Project has not been saved since it was opened.")
    
    save_ago = (time.clock() - useraction.save_time) / 60.0

    if save_ago < 1:
        return _("Project was saved less than a minute ago.")

    if save_ago < 2:
        return _("Project was saved one minute ago.")
    
    return _("Project was saved ") + str(int(save_ago)) + _(" minutes ago.")

def _shutdown_dialog_callback(dialog, response_id):
    dialog.destroy()
    if response_id == gtk.RESPONSE_CLOSE:# "Don't Save"
        pass
    elif response_id ==  gtk.RESPONSE_YES:# "Save"
        if editorstate.PROJECT().last_save_path != None:
            persistance.save_project(editorstate.PROJECT(), editorstate.PROJECT().last_save_path)
        else:
            dialogs.warning_message(_("Project has not been saved previously"), 
                                    _("Save project with File -> Save As before closing."),
                                    gui.editor_window.window)
            return
    else: # "Cancel"
        return

    # --- APP SHUT DOWN --- #
    print "exiting app..."

    # No more auto saving
    stop_autosave()
    
    # Block reconnecting consumer before setting window not visible
    updater.player_refresh_enabled = False
    gui.editor_window.window.set_visible(False)

    # Wait window to be hidden or it will freeze before disappering
    while(gtk.events_pending()):
        gtk.main_iteration()
 
    # Close threads and stop mlt consumers
    projectdata.thumbnail_thread.shutdown()
    editorstate.player.shutdown() # has ticker thread and player threads running

    # Wait threads to stop
    while((editorstate.player.running == True) and (editorstate.player.ticker.exited == False)
          and(projectdata.thumbnail_thread.stopped == False)):
        pass

    # Delete autosave file
    try:
        os.remove(utils.get_hidden_user_dir_path() + AUTOSAVE_FILE)
    except:
        pass

    # Exit gtk main loop.
    gtk.main_quit()
    
