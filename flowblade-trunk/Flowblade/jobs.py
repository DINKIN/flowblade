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


from gi.repository import Gtk
from gi.repository import GObject
from gi.repository import Pango

import copy
import time

import editorpersistance
import gui
import guicomponents
import guiutils
import utils

QUEUED = 0
RENDERING = 1
COMPLETED = 2
CANCELLED = 3

NOT_SET_YET = 0
CONTAINER_CLIP_RENDER_GMIC = 1
CONTAINER_CLIP_RENDER_MLT_XML = 2
CONTAINER_CLIP_RENDER_BLENDER = 3


_hamburger_menu = Gtk.Menu()

_jobs_list_view = None

_jobs = [] # proxy objects that represent background renders and provide info on render status.

_remove_list = [] # objects are removed from GUI with delay to give user time to notice copmpletion

jobs_notebook_index = 4 # 4 for single window, app.py sets to 3 for two windows


class JobProxy: # Background renders provide these to give info on render status.
                  # Modules doing the rendering must manage setting all values.

    def __init__(self, uid, callback_object):
        self.proxy_uid = uid # modules doing the rendering and using this to display must make sure this matches always for a particular job
        self.type = NOT_SET_YET 
        self.status = RENDERING
        self.progress = 0.0 # 0.0. - 1.0
        self.text = ""
        self.elapsed = 0.0 # in fractional seconds

        # callback_object reqiured to implement interface:
        #     start_render()
        #     abort_render()
        self.callback_object = callback_object

    def get_elapsed_str(self):
        return utils.get_time_str_for_sec_float(self.elapsed)

    def get_type_str(self):
        c_clip_str = _("Container Clip")
        if self.type == NOT_SET_YET:
            return "NO TYPE SET" # this just error info, application has done something wrong.
        elif self.type == CONTAINER_CLIP_RENDER_GMIC:
            return c_clip_str + " " +  "G'Mic"
        elif self.type == CONTAINER_CLIP_RENDER_MLT_XML:
            return  c_clip_str + " " +  "MLT XML"
        elif self.type == CONTAINER_CLIP_RENDER_BLENDER:
            return c_clip_str + " " + "Blender"
            
    def get_progress_str(self):
        if self.progress < 0.0:
            return "-"
        return str(int(self.progress * 100.0)) + "%"

    def start_render(self):
        self.callback_object.start_render()
        
    def abort_render(self):
        self.callback_object.abort_render()


#---------------------------------------------------------------- interface
def add_job(job_proxy):
    global _jobs, _jobs_list_view 
    _jobs.append(job_proxy)
    _jobs_list_view.fill_data_model()
    if editorpersistance.prefs.open_jobs_panel_on_add == True:
        gui.middle_notebook.set_current_page(jobs_notebook_index)
    
    if editorpersistance.prefs.render_jobs_sequentially == False:
        job_proxy.start_render()
    else:
         running = _get_jobs_with_status(RENDERING)
         if len(running) == 0:
             job_proxy.start_render()
            
def update_job_queue(update_msg_job_proxy): # We're using JobProxy objects as messages to update values on jobs in _jobs list.
    global _jobs_list_view, _remove_list
    row = -1
    job_proxy = None  
    for i in range (0, len(_jobs)):
        #job_proxy = _jobs[i]

        if _jobs[i].proxy_uid == update_msg_job_proxy.proxy_uid:
            if _jobs[i].status == CANCELLED:
                return # it is maybe possible to get update attempt here after cancellation.         
            # Update job proxy info and remember row
            #job_proxy = copy.copy(update_job_proxy)
            row = i
            break

    if row == -1:
        # Something is wrong.
        print("trying to update non-existing job at jobs.show_message()!")
        return

    # Copy values
    _jobs[row].text = update_msg_job_proxy.text
    _jobs[row].elapsed = update_msg_job_proxy.elapsed
    _jobs[row].progress = update_msg_job_proxy.progress

    if update_msg_job_proxy.status == COMPLETED:
        _jobs[row].status = COMPLETED
        _jobs[row].text = _("Completed")
        _jobs[row].progress = 1.0
        _remove_list.append(_jobs[row])
        GObject.timeout_add(4000, _remove_jobs)
        waiting_jobs = _get_jobs_with_status(QUEUED)
        if len(waiting_jobs) > 0:
            waiting_jobs[0].start_render()
    else:
        _jobs[row].status = update_msg_job_proxy.status

    tree_path = Gtk.TreePath.new_from_string(str(row))
    store_iter = _jobs_list_view.storemodel.get_iter(tree_path)

    _jobs_list_view.storemodel.set_value(store_iter, 0, _jobs[row].get_type_str())
    _jobs_list_view.storemodel.set_value(store_iter, 1, _jobs[row].text)
    _jobs_list_view.storemodel.set_value(store_iter, 2, _jobs[row].get_elapsed_str())
    _jobs_list_view.storemodel.set_value(store_iter, 3, _jobs[row].get_progress_str())

    _jobs_list_view.scroll.queue_draw()

def create_jobs_list_view():
    global _jobs_list_view
    _jobs_list_view = JobsQueueView()
    return _jobs_list_view

def get_jobs_panel():
    global _jobs_list_view #, widgets

    actions_menu = guicomponents.HamburgerPressLaunch(_menu_action_pressed)
    guiutils.set_margins(actions_menu.widget, 8, 2, 2, 18)

    row2 =  Gtk.HBox()
    row2.pack_start(actions_menu.widget, False, True, 0)
    row2.pack_start(Gtk.Label(), True, True, 0)

    panel = Gtk.VBox()
    panel.pack_start(_jobs_list_view, True, True, 0)
    panel.pack_start(row2, False, True, 0)
    panel.set_size_request(400, 10)

    return panel


# ------------------------------------------------------------- module functions
def _menu_action_pressed(widget, event):
    menu = _hamburger_menu
    guiutils.remove_children(menu)
    menu.add(guiutils.get_menu_item(_("Cancel Selected Render"), _hamburger_item_activated, "cancel_selected"))
    menu.add(guiutils.get_menu_item(_("Cancel All Renders"), _hamburger_item_activated, "cancel_all"))
    
    guiutils.add_separetor(menu)

    sequential_render_item = Gtk.CheckMenuItem()
    sequential_render_item.set_label(_("Render All Jobs Sequentially"))
    sequential_render_item.set_active(editorpersistance.prefs.render_jobs_sequentially)
    sequential_render_item.connect("activate", _hamburger_item_activated, "sequential_render")
    menu.add(sequential_render_item)
    
    open_on_add_item = Gtk.CheckMenuItem()
    open_on_add_item.set_label(_("Show Jobs Panel on Adding New Job"))
    open_on_add_item.set_active(editorpersistance.prefs.open_jobs_panel_on_add)
    open_on_add_item.connect("activate", _hamburger_item_activated, "open_on_add")
    menu.add(open_on_add_item)
    
    menu.show_all()
    menu.popup(None, None, None, None, event.button, event.time)

def _hamburger_item_activated(widget, msg):
    if msg == "cancel_all":
        global _jobs, _remove_list
        _remove_list = []
        for job in _get_jobs_with_status(RENDERING):
            job.abort_render()
            job.progress = -1.0
            job.text = _("Cancelled")
            job.status = CANCELLED
            _remove_list.append(job)

        _jobs_list_view.fill_data_model()
        _jobs_list_view.scroll.queue_draw()
        GObject.timeout_add(4000, _remove_jobs)

    elif msg == "cancel_selected":
        jobs_list_index = _jobs_list_view.get_selected_row_index()
        
        job = _jobs[jobs_list_index]
        job.abort_render()
        job.progress = -1.0
        job.text = _("Cancelled")
        job.status = CANCELLED
        _remove_list.append(job)

        _jobs_list_view.fill_data_model()
        _jobs_list_view.scroll.queue_draw()
        GObject.timeout_add(4000, _remove_jobs)
        
    elif msg == "open_on_add":
        editorpersistance.prefs.open_jobs_panel_on_add = widget.get_active()
        editorpersistance.save()

    elif msg == "sequential_render":
        editorpersistance.prefs.render_jobs_sequentially = widget.get_active()
        editorpersistance.save()

def _get_jobs_with_status(status):
    running = []
    for job in _jobs:
        if job.status == status:
            running.append(job)
    
    return running

def _remove_jobs():
    global _jobs, _remove_list
    for  job in _remove_list:
        _jobs.remove(job)

    _jobs_list_view.fill_data_model()
    _jobs_list_view.scroll.queue_draw()

    _remove_list = []

# --------------------------------------------------------- GUI 
class JobsQueueView(Gtk.VBox):

    def __init__(self):
        GObject.GObject.__init__(self)
        
        self.storemodel = Gtk.ListStore(str, str, str, str)
        
        # Scroll container
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_shadow_type(Gtk.ShadowType.ETCHED_IN)

        # View
        self.treeview = Gtk.TreeView(self.storemodel)
        self.treeview.set_property("rules_hint", True)
        self.treeview.set_headers_visible(True)
        tree_sel = self.treeview.get_selection()
        tree_sel.set_mode(Gtk.SelectionMode.MULTIPLE)

        self.text_rend_1 = Gtk.CellRendererText()
        self.text_rend_1.set_property("ellipsize", Pango.EllipsizeMode.END)

        self.text_rend_2 = Gtk.CellRendererText()
        self.text_rend_2.set_property("yalign", 0.0)
        self.text_rend_2.set_property("ellipsize", Pango.EllipsizeMode.END)
        
        self.text_rend_3 = Gtk.CellRendererText()
        self.text_rend_3.set_property("yalign", 0.0)
        
        self.text_rend_4 = Gtk.CellRendererText()
        self.text_rend_4.set_property("yalign", 0.0)

        # Column views
        self.text_col_1 = Gtk.TreeViewColumn(_("Type"))
        self.text_col_2 = Gtk.TreeViewColumn(_("Info"))
        self.text_col_3 = Gtk.TreeViewColumn(_("Render Time"))
        self.text_col_4 = Gtk.TreeViewColumn(_("Progress"))

        #self.text_col_1.set_expand(True)
        self.text_col_1.set_spacing(5)
        self.text_col_1.set_sizing(Gtk.TreeViewColumnSizing.GROW_ONLY)
        self.text_col_1.set_min_width(200)
        self.text_col_1.pack_start(self.text_rend_1, True)
        self.text_col_1.add_attribute(self.text_rend_1, "text", 0) # <- note column index

        self.text_col_2.set_expand(True)
        self.text_col_2.pack_start(self.text_rend_2, True)
        self.text_col_2.add_attribute(self.text_rend_2, "text", 1)
        self.text_col_2.set_min_width(90)

        self.text_col_3.set_expand(False)
        self.text_col_3.pack_start(self.text_rend_3, True)
        self.text_col_3.add_attribute(self.text_rend_3, "text", 2)

        self.text_col_4.set_expand(False)
        self.text_col_4.pack_start(self.text_rend_4, True)
        self.text_col_4.add_attribute(self.text_rend_4, "text", 3)

        # Add column views to view
        self.treeview.append_column(self.text_col_1)
        self.treeview.append_column(self.text_col_2)
        self.treeview.append_column(self.text_col_3)
        self.treeview.append_column(self.text_col_4)

        # Build widget graph and display
        self.scroll.add(self.treeview)
        self.pack_start(self.scroll, True, True, 0)
        self.scroll.show_all()
        self.show_all()

    def get_selected_row_index(self):
        model, rows = self.treeview.get_selection().get_selected_rows()
        return int(rows[0].to_string ())
        
    def fill_data_model(self):
        self.storemodel.clear()        
        
        for job in _jobs:
            row_data = [job.get_type_str(),
                        job.text,
                        job.get_elapsed_str(),
                        job.get_progress_str()]
            self.storemodel.append(row_data)
            self.scroll.queue_draw()
