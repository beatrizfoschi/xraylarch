#!/usr/bin/env python
"""
XANES Data Viewer and Analysis Tool
"""
import os
import sys
import time
import copy
import platform
import numpy as np
np.seterr(all='ignore')

from functools import partial

import wx
import wx.lib.scrolledpanel as scrolled

import wx.lib.mixins.inspection
from wx.adv import AboutBox, AboutDialogInfo

from wx.richtext import RichTextCtrl

WX_DEBUG = True

import larch
from larch import Group, Journal, Entry
from larch.math import index_of
from larch.utils import isotime, get_cwd
from larch.utils.strutils import (file2groupname, unique_name,
                                  common_startstring)

from larch.larchlib import read_workdir, save_workdir, read_config, save_config

from larch.wxlib import (LarchFrame, ColumnDataFileFrame, AthenaImporter,
                         SpecfileImporter, FileCheckList, FloatCtrl,
                         SetTip, get_icon, SimpleText, pack, Button, Popup,
                         HLine, FileSave, Choice, Check, MenuItem,
                         GUIColors, CEN, LEFT, FRAMESTYLE, Font, FONTSIZE,
                         flatnotebook, LarchUpdaterDialog,
                         CIFFrame, FeffResultsFrame, LarchWxApp)
from larch.wxlib.plotter import _getDisplay

from larch.fitting import fit_report
from larch.site_config import icondir

from .prepeak_panel import PrePeakPanel
from .xasnorm_panel import XASNormPanel
from .lincombo_panel import LinearComboPanel
from .pca_panel import PCAPanel
from .exafs_panel import EXAFSPanel
from .feffit_panel import FeffitPanel
from .regress_panel import RegressionPanel
from .xas_controller import XASController

from .xas_dialogs import (MergeDialog, RenameDialog, RemoveDialog,
                          DeglitchDialog, ExportCSVDialog, RebinDataDialog,
                          EnergyCalibrateDialog, SmoothDataDialog,
                          OverAbsorptionDialog, DeconvolutionDialog,
                          SpectraCalcDialog,  QuitDialog)

from larch.io import (read_ascii, read_xdi, read_gsexdi, gsescan_group,
                      fix_varname, groups2csv, is_athena_project,
                      AthenaProject, make_hashkey, is_specfile, open_specfile)

from larch.xafs import pre_edge, pre_edge_baseline

LEFT = wx.ALIGN_LEFT
CEN |=  wx.ALL
FILE_WILDCARDS = "Data Files(*.0*,*.dat,*.xdi,*.prj,*.spc,*.hdf5)|*.0*;*.dat;*.DAT;*.xdi;*.prj;*.sp*c;*.h*5|All files (*.*)|*.*"

ICON_FILE = 'onecone.ico'
XASVIEW_SIZE = (990, 750)
PLOTWIN_SIZE = (550, 550)

NB_PANELS = {'Normalization': XASNormPanel,
             'Pre-edge Peak': PrePeakPanel,
             'PCA':  PCAPanel,
             'Linear Combo': LinearComboPanel,
             'Regression': RegressionPanel,
             'EXAFS':  EXAFSPanel,
             'Feff Fitting': FeffitPanel}

QUIT_MESSAGE = '''Really Quit? You may want to save your project before quitting.
 This is not done automatically!'''


def assign_gsescan_groups(group):
    labels = group.array_labels
    labels = []
    for i, name in enumerate(group.pos_desc):
        name = fix_varname(name.lower())
        labels.append(name)
        setattr(group, name, group.pos[i, :])

    for i, name in enumerate(group.sums_names):
        name = fix_varname(name.lower())
        labels.append(name)
        setattr(group, name, group.sums_corr[i, :])

    for i, name in enumerate(group.det_desc):
        name = fix_varname(name.lower())
        labels.append(name)
        setattr(group, name, group.det_corr[i, :])

    group.array_labels = labels


class XASFrame(wx.Frame):
    _about = """Larch XAS GUI: XAS Visualization and Analysis

    Matt Newville <newville @ cars.uchicago.edu>
    """
    def __init__(self, parent=None, _larch=None, filename=None,
                 version_info=None, **kws):
        wx.Frame.__init__(self, parent, -1, size=XASVIEW_SIZE, style=FRAMESTYLE)

        self.last_array_sel_col = {}
        self.last_array_sel_spec = {}
        self.last_project_file = None
        self.paths2read = []
        self.current_filename = filename
        self.extra_sums = None
        title = "Larch XAS GUI: XAS Visualization and Analysis"

        self.larch_buffer = parent
        if not isinstance(parent, LarchFrame):
            self.larch_buffer = LarchFrame(_larch=_larch, is_standalone=False, with_raise=False)

        self.larch = self.larch_buffer.larchshell
        self.larch.symtable._sys.xas_viewer = Group()

        self.controller = XASController(wxparent=self, _larch=self.larch)
        iconfile = os.path.join(icondir, ICON_FILE)
        self.SetIcon(wx.Icon(iconfile, wx.BITMAP_TYPE_ICO))

        self.larch.symtable._sys.xas_viewer.mainframe = self

        self.timers = {'pin': wx.Timer(self)}
        self.Bind(wx.EVT_TIMER, self.onPinTimer, self.timers['pin'])
        self.cursor_dat = {}

        self.subframes = {}
        self.plotframe = None
        self.SetTitle(title)
        self.SetSize(XASVIEW_SIZE)

        self.SetFont(Font(FONTSIZE))
        self.createMainPanel()
        self.createMenus()
        self.statusbar = self.CreateStatusBar(2, style=wx.STB_DEFAULT_STYLE)
        self.statusbar.SetStatusWidths([-3, -1])
        statusbar_fields = [" ", "ready"]
        for i in range(len(statusbar_fields)):
            self.statusbar.SetStatusText(statusbar_fields[i], i)
        self.Show()
        if version_info is not None:
            if version_info.update_available:
                self.onCheckforUpdates()

        plotframe = self.controller.get_display(stacked=False)
        xpos, ypos = self.GetPosition()
        xsiz, ysiz = self.GetSize()
        plotframe.SetPosition((xpos+xsiz+5, ypos))

        self.Raise()
        self.statusbar.SetStatusText('ready', 1)
        if self.current_filename is not None:
            wx.CallAfter(self.onRead, self.current_filename)


    def createMainPanel(self):
        display0 = wx.Display(0)
        client_area = display0.ClientArea
        xmin, ymin, xmax, ymax = client_area
        xpos = int((xmax-xmin)*0.02) + xmin
        ypos = int((ymax-ymin)*0.04) + ymin
        self.SetPosition((xpos, ypos))

        splitter  = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        splitter.SetMinimumPaneSize(250)

        leftpanel = wx.Panel(splitter)
        ltop = wx.Panel(leftpanel)

        def Btn(msg, x, act):
            b = Button(ltop, msg, size=(x, 30),  action=act)
            b.SetFont(Font(FONTSIZE))
            return b

        sel_none = Btn('Select None',   120, self.onSelNone)
        sel_all  = Btn('Select All',    120, self.onSelAll)

        self.controller.filelist = FileCheckList(leftpanel,
                                                 select_action=self.ShowFile,
                                                 remove_action=self.RemoveFile)
        tsizer = wx.BoxSizer(wx.HORIZONTAL)
        tsizer.Add(sel_all, 1, LEFT|wx.GROW, 1)
        tsizer.Add(sel_none, 1, LEFT|wx.GROW, 1)
        pack(ltop, tsizer)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(ltop, 0, LEFT|wx.GROW, 1)
        sizer.Add(self.controller.filelist, 1, LEFT|wx.GROW|wx.ALL, 1)

        pack(leftpanel, sizer)

        # right hand side
        panel = wx.Panel(splitter)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.title = SimpleText(panel, ' ', size=(500, 25),
                                font=Font(FONTSIZE+3), style=LEFT,
                                colour=wx.Colour(10, 10, 180))

        ir = 0
        sizer.Add(self.title, 0, CEN, 3)
        self.nb = flatnotebook(panel, NB_PANELS,
                               panelkws=dict(xasmain=self,
                                             controller=self.controller),
                               on_change=self.onNBChanged)

        sizer.Add(self.nb, 1, LEFT|wx.EXPAND, 2)
        pack(panel, sizer)
        splitter.SplitVertically(leftpanel, panel, 1)

    def process_normalization(self, dgroup, force=True):
        self.get_nbpage('xasnorm')[1].process(dgroup, force=force)

    def process_exafs(self, dgroup, force=True):
        self.get_nbpage('exafs')[1].process(dgroup, force=force)

    def get_nbpage(self, name):
        "get nb page by name"
        name = name.lower()
        out = (0, self.nb.GetCurrentPage())
        for i, page in enumerate(self.nb.pagelist):
            if name in page.__class__.__name__.lower():
                out = (i, page)
        return out

    def onNBChanged(self, event=None):
        callback = getattr(self.nb.GetCurrentPage(), 'onPanelExposed', None)
        if callable(callback):
            callback()

    def onSelAll(self, event=None):
        self.controller.filelist.select_all()

    def onSelNone(self, event=None):
        self.controller.filelist.select_none()

    def write_message(self, msg, panel=0):
        """write a message to the Status Bar"""
        self.statusbar.SetStatusText(msg, panel)

    def RemoveFile(self, fname=None, **kws):
        if fname is not None:
            s = str(fname)
            if s in self.controller.file_groups:
                group = self.controller.file_groups.pop(s)

    def ShowFile(self, evt=None, groupname=None, process=True,
                 filename=None, plot=True, **kws):
        if filename is None and evt is not None:
            filename = str(evt.GetString())

        if groupname is None and filename is not None:
            groupname = self.controller.file_groups[filename]

        if not hasattr(self.larch.symtable, groupname):
            return

        dgroup = self.controller.get_group(groupname)
        if dgroup is None:
            return

        if filename is None:
            filename = dgroup.filename
        self.current_filename = filename
        journal = getattr(dgroup, 'journal', Journal(source_desc=filename))
        print("JOURNAL ", dgroup, journal)
        sdesc = journal.get('source_desc', latest=True)
        if isinstance(sdesc, Entry):
            sdesc = sdesc.value
        if not isinstance(sdesc, str):
            sdesc = repr(sdesc)
        self.title.SetLabel(sdesc)

        self.controller.group = dgroup
        self.controller.groupname = groupname
        cur_panel = self.nb.GetCurrentPage()
        if process:
            cur_panel.fill_form(dgroup)
            cur_panel.skip_process = False
            cur_panel.process(dgroup=dgroup)
            if plot and hasattr(cur_panel, 'plot'):
                cur_panel.plot(dgroup=dgroup)
            cur_panel.skip_process = False


    def createMenus(self):
        # ppnl = self.plotpanel
        self.menubar = wx.MenuBar()
        fmenu = wx.Menu()
        group_menu = wx.Menu()
        data_menu = wx.Menu()
        feff_menu = wx.Menu()
        ppeak_menu = wx.Menu()
        m = {}

        MenuItem(self, fmenu, "&Open Data File\tCtrl+O",
                 "Open Data File",  self.onReadDialog)

        MenuItem(self, fmenu, "&Save Project\tCtrl+S",
                 "Save Session to Project File",  self.onSaveProject)

        MenuItem(self, fmenu, "&Save Project As...",
                 "Save Session to a new Project File",  self.onSaveAsProject)

        MenuItem(self, fmenu, "Export Selected Groups to Project File",
                 "Export Selected Groups to Project File",
                 self.onExportProject)

        MenuItem(self, fmenu, "Export Selected Groups to CSV",
                 "Export Selected Groups to CSV",
                 self.onExportCSV)

        fmenu.AppendSeparator()

        MenuItem(self, fmenu, 'Show Larch Buffer\tCtrl+L',
                 'Show Larch Programming Buffer',
                 self.onShowLarchBuffer)

        MenuItem(self, fmenu, 'Save Larch Script of History\tCtrl+H',
                 'Save Session History as Larch Script',
                 self.onSaveLarchHistory)

        if WX_DEBUG:
            MenuItem(self, fmenu, "&Inspect \tCtrl+J",
                     " wx inspection tool ",  self.showInspectionTool)

        MenuItem(self, fmenu, "&Quit\tCtrl+Q", "Quit program", self.onClose)


        MenuItem(self, group_menu, "Copy This Group",
                 "Copy This Group", self.onCopyGroup)

        MenuItem(self, group_menu, "Rename This Group",
                 "Rename This Group", self.onRenameGroup)

        MenuItem(self, group_menu, "Show Group Journal",
                 "Show Processing Journal for This Group", self.onGroupJournal)


        group_menu.AppendSeparator()

        MenuItem(self, group_menu, "Remove Selected Groups",
                 "Remove Selected Group", self.onRemoveGroups)

        group_menu.AppendSeparator()

        MenuItem(self, group_menu, "Merge Selected Groups",
                 "Merge Selected Groups", self.onMergeData)

        group_menu.AppendSeparator()

        MenuItem(self, group_menu, "Freeze Selected Groups",
                 "Freeze Selected Groups", self.onFreezeGroups)

        MenuItem(self, group_menu, "UnFreeze Selected Groups",
                 "UnFreeze Selected Groups", self.onUnFreezeGroups)

        MenuItem(self, data_menu, "Deglitch Data",  "Deglitch Data",
                 self.onDeglitchData)

        MenuItem(self, data_menu, "Calibrate Energy",
                 "Calibrate Energy",
                 self.onEnergyCalibrateData)

        MenuItem(self, data_menu, "Smooth Data", "Smooth Data",
                 self.onSmoothData)

        MenuItem(self, data_menu, "Rebin Data", "Rebin Data",
                 self.onRebinData)

        MenuItem(self, data_menu, "Deconvolve Data",
                 "Deconvolution of Data",  self.onDeconvolveData)

        MenuItem(self, data_menu, "Correct Over-absorption",
                 "Correct Over-absorption",
                 self.onCorrectOverAbsorptionData)

        MenuItem(self, data_menu, "Add and Subtract Spectra",
                 "Calculations of Spectra",  self.onSpectraCalc)

        MenuItem(self, ppeak_menu, "Load Pre-edge Peak Model",
                 "Load saved model for Pre-edge Peak Fitting",
                 self.onPrePeakLoad)

        self.menubar.Append(fmenu, "&File")
        self.menubar.Append(group_menu, "Groups")
        self.menubar.Append(data_menu, "Data")
        self.menubar.Append(ppeak_menu, "Pre-edge Peaks")

        MenuItem(self, feff_menu, "Browse CIF Structures, Run Feff",
                 "Browse CIF Structure, run Feff", self.onCIFBrowse)
        MenuItem(self, feff_menu, "Browse Feff Calculations",
                 "Browse Feff Calculations, Get Feff Paths", self.onFeffBrowse)

        self.menubar.Append(feff_menu, "Feff")

        hmenu = wx.Menu()
        MenuItem(self, hmenu, 'About XAS Viewer', 'About XAS Viewer',
                 self.onAbout)
        MenuItem(self, hmenu, 'Check for Updates', 'Check for Updates',
                 self.onCheckforUpdates)

        self.menubar.Append(hmenu, '&Help')
        self.SetMenuBar(self.menubar)
        self.Bind(wx.EVT_CLOSE,  self.onClose)

    def onShowLarchBuffer(self, evt=None):
        if self.larch_buffer is None:
            self.larch_buffer = LarchFrame(_larch=self.larch, is_standalone=False)
        self.larch_buffer.Show()
        self.larch_buffer.Raise()

    def onSaveLarchHistory(self, evt=None):
        wildcard = 'Larch file (*.lar)|*.lar|All files (*.*)|*.*'
        path = FileSave(self, message='Save Session History as Larch Script',
                        wildcard=wildcard,
                        default_file='xas_viewer_history.lar')
        if path is not None:
            self.larch._larch.input.history.save(path, session_only=True)
            self.write_message("Wrote history %s" % path, 0)

    def onExportCSV(self, evt=None):
        filenames = self.controller.filelist.GetCheckedStrings()
        if len(filenames) < 1:
            Popup(self, "No files selected to export to CSV",
                  "No files selected")
            return

        deffile = "%s_%i.csv" % (filenames[0], len(filenames))

        dlg = ExportCSVDialog(self, filenames)
        res = dlg.GetResponse()

        dlg.Destroy()
        if not res.ok:
            return

        deffile = f"{filenames[0]:s}_{len(filenames):d}.csv"
        wcards  = 'CSV Files (*.csv)|*.csv|All files (*.*)|*.*'

        outfile = FileSave(self, 'Save Groups to CSV File',
                           default_file=deffile, wildcard=wcards)

        if outfile is None:
            return
        if os.path.exists(outfile):
            if wx.ID_YES != Popup(self,
                                  "Overwrite existing CSV File?",
                                  "Overwrite existing file?", style=wx.YES_NO):
                return

        savegroups = [self.controller.filename2group(res.master)]
        for fname in filenames:
            dgroup = self.controller.filename2group(fname)
            if dgroup not in savegroups:
                savegroups.append(dgroup)


        groups2csv(savegroups, outfile, x='energy', y=res.yarray,
                   delim=res.delim, _larch=self.larch)
        self.write_message(f"Exported CSV file {outfile:s}")

    def onExportProject(self, evt=None):
        groups = []
        for checked in self.controller.filelist.GetCheckedStrings():
            groups.append(self.controller.file_groups[str(checked)])

        if len(groups) < 1:
             Popup(self, "No files selected to export to Project",
                   "No files selected")
             return
        prompt, prjfile = self.get_projectfile()
        self.save_athena_project(prjfile, groups)

    def get_projectfile(self):
        prjfile = self.last_project_file
        prompt = False
        if prjfile is None:
            tstamp = isotime(filename=True)[:15]
            prjfile = f"{tstamp:s}.prj"
            prompt = True
        return prompt, prjfile

    def onSaveProject(self, evt=None):
        groups = self.controller.filelist.GetItems()
        if len(groups) < 1:
            Popup(self, "No files to export to Project", "No files to export")
            return

        prompt, prjfile = self.get_projectfile()
        self.save_athena_project(prjfile, groups, prompt=prompt,
                                 warn_overwrite=False)

    def onSaveAsProject(self, evt=None):
        groups = self.controller.filelist.GetItems()
        if len(groups) < 1:
            Popup(self, "No files to export to Project", "No files to export")
            return

        prompt, prjfile = self.get_projectfile()
        self.save_athena_project(prjfile, groups)

    def save_athena_project(self, filename, grouplist, prompt=True,
                            warn_overwrite=True):
        if len(grouplist) < 1:
            return
        savegroups = [self.controller.get_group(gname) for gname in grouplist]
        if prompt:
            _, filename = os.path.split(filename)
            wcards  = 'Project Files (*.prj)|*.prj|All files (*.*)|*.*'
            filename = FileSave(self, 'Save Groups to Project File',
                                default_file=filename, wildcard=wcards)
            if filename is None:
                return

        if os.path.exists(filename) and warn_overwrite:
            if wx.ID_YES != Popup(self,
                                  "Overwrite existing Project File?",
                                  "Overwrite existing file?", style=wx.YES_NO):
                return

        aprj = AthenaProject(filename=filename, _larch=self.larch)
        for label, grp in zip(grouplist, savegroups):
            aprj.add_group(grp)
        aprj.save(use_gzip=True)
        self.write_message("Saved project file %s" % (filename))
        self.last_project_file = filename

    def onConfigDataProcessing(self, event=None):
        pass

    def onNewGroup(self, datagroup, source=None):
        """
        install and display a new group, as from 'copy / modify'
        Note: this is a group object, not the groupname or filename
        """
        dgroup = datagroup
        self.install_group(dgroup.groupname, dgroup.filename, source=source, overwrite=False)
        self.ShowFile(groupname=dgroup.groupname)

    def onCopyGroup(self, event=None):
        fname = self.current_filename
        if fname is None:
            fname = self.controller.filelist.GetStringSelection()
        ngroup = self.controller.copy_group(fname, source=f"copied from '{fname:s}'")
        self.onNewGroup(ngroup)

    def onGroupJournal(self, event=None):
        dgroup = self.controller.get_group()
        print("show journal for group ", dgroup)
        #if dgroup is not None:
        #  self.show_subframe('group_journal', JournalFrame, dgroup, _larch=self.larch)


    def onRenameGroup(self, event=None):
        fname = self.current_filename = self.controller.filelist.GetStringSelection()
        if fname is None:
            return
        dlg = RenameDialog(self, fname)
        res = dlg.GetResponse()
        dlg.Destroy()

        if res.ok:
            selected = []
            for checked in self.controller.filelist.GetCheckedStrings():
                selected.append(str(checked))
            if self.current_filename in selected:
                selected.remove(self.current_filename)
                selected.append(res.newname)

            groupname = self.controller.file_groups.pop(fname)
            self.controller.file_groups[res.newname] = groupname
            self.controller.filelist.rename_item(self.current_filename, res.newname)
            dgroup = self.controller.get_group(groupname)
            dgroup.filename = self.current_filename = res.newname

            self.controller.filelist.SetCheckedStrings(selected)
            self.controller.filelist.SetStringSelection(res.newname)

    def onRemoveGroups(self, event=None):
        groups = []
        for checked in self.controller.filelist.GetCheckedStrings():
            groups.append(str(checked))
        if len(groups) < 1:
            return

        dlg = RemoveDialog(self, groups)
        res = dlg.GetResponse()
        dlg.Destroy()

        if res.ok:
            filelist = self.controller.filelist
            all_fnames = filelist.GetItems()
            for fname in groups:
                gname = self.controller.file_groups.pop(fname)
                delattr(self.controller.symtable, gname)
                all_fnames.remove(fname)

            filelist.Clear()
            for name in all_fnames:
                filelist.Append(name)

    def onFreezeGroups(self, event=None):
        self._freeze_handler(True)

    def onUnFreezeGroups(self, event=None):
        self._freeze_handler(False)

    def _freeze_handler(self, freeze):
        current_filename = self.current_filename
        reproc_group = None
        for fname in self.controller.filelist.GetCheckedStrings():
            groupname = self.controller.file_groups[str(fname)]
            dgroup = self.controller.get_group(groupname)
            if fname == current_filename:
                reproc_group = groupname
            dgroup.is_frozen = freeze
        if reproc_group is not None:
            self.ShowFile(groupname=reproc_group, process=True)

    def onMergeData(self, event=None):
        groups = {}
        for checked in self.controller.filelist.GetCheckedStrings():
            cname = str(checked)
            groups[cname] = self.controller.file_groups[cname]
        if len(groups) < 1:
            return

        outgroup = common_startstring(list(groups.keys()))
        outgroup = "%s (merge %d)" % (outgroup, len(groups))
        outgroup = unique_name(outgroup, self.controller.file_groups)
        dlg = MergeDialog(self, list(groups.keys()), outgroup=outgroup)
        res = dlg.GetResponse()
        dlg.Destroy()
        if res.ok:
            fname = res.group
            gname = fix_varname(res.group.lower())
            master = self.controller.file_groups[res.master]
            yname = 'norm' if res.ynorm else 'mu'
            self.controller.merge_groups(list(groups.values()),
                                         master=master,
                                         yarray=yname,
                                         outgroup=gname)
            self.install_group(gname, fname, overwrite=False,
                               source="merge of %n groups" % len(groups),
                               journal={'merged_groups': repr(list(groups.values()))})
            self.controller.filelist.SetStringSelection(fname)

    def onDeglitchData(self, event=None):
        DeglitchDialog(self, self.controller).Show()

    def onSmoothData(self, event=None):
        SmoothDataDialog(self, self.controller).Show()

    def onRebinData(self, event=None):
        RebinDataDialog(self, self.controller).Show()

    def onCorrectOverAbsorptionData(self, event=None):
        OverAbsorptionDialog(self, self.controller).Show()

    def onSpectraCalc(self, event=None):
        SpectraCalcDialog(self, self.controller).Show()

    def onEnergyCalibrateData(self, event=None):
        EnergyCalibrateDialog(self, self.controller).Show()

    def onDeconvolveData(self, event=None):
        DeconvolutionDialog(self, self.controller).Show()

    def onPrePeakLoad(self, event=None):
        idx, peakpage = self.get_nbpage('prepeak')
        self.nb.SetSelection(idx)
        peakpage.onLoadFitResult()

    def onConfigDataFitting(self, event=None):
        pass

    def showInspectionTool(self, event=None):
        app = wx.GetApp()
        app.ShowInspectionTool()

    def onAbout(self, event=None):
        info = AboutDialogInfo()
        info.SetName('XAS Viewer')
        info.SetDescription('X-ray Absorption Visualization and Analysis')
        info.SetVersion('Larch %s ' % larch.version.__version__)
        info.AddDeveloper('Matthew Newville: newville at cars.uchicago.edu')
        dlg = AboutBox(info)

    def onCheckforUpdates(self, event=None):
        dlg = LarchUpdaterDialog(self, caller='XAS Viewer')
        dlg.Raise()
        dlg.SetWindowStyle(wx.STAY_ON_TOP)
        res = dlg.GetResponse()
        dlg.Destroy()
        if res.ok and res.run_updates:
            from larch.apps import update_larch
            update_larch()
            self.onClose(event=event, prompt=False)

    def onClose(self, event=None, prompt=True):
        if prompt:
            dlg = QuitDialog(self)
            dlg.Raise()
            dlg.SetWindowStyle(wx.STAY_ON_TOP)
            res = dlg.GetResponse()
            dlg.Destroy()
            if not res.ok:
                return

            if res.save:
                groups = [gname for gname in self.controller.file_groups]
                if len(groups) > 0:
                    self.save_athena_project(groups[0], groups, prompt=True)

        self.controller.save_config()
        try:
            self.controller.close_all_displays()
        except Exception:
            pass

        if self.larch_buffer is not None:
            try:
                self.larch_buffer.Destroy()
            except Exception:
                pass

        def destroy(wid):
            if hasattr(wid, 'Destroy'):
                try:
                    wid.Destroy()
                except Exception:
                    pass
                time.sleep(0.01)

        for name, wid in self.subframes.items():
            destroy(wid)

        for i in range(self.nb.GetPageCount()):
            nbpage = self.nb.GetPage(i)
            timers = getattr(nbpage, 'timers', None)
            if timers is not None:
                for t in timers.values():
                    t.Stop()

            if hasattr(nbpage, 'subframes'):
                for name, wid in nbpage.subframes.items():
                    destroy(wid)
        for t in self.timers.values():
            t.Stop()

        time.sleep(0.05)
        self.Destroy()

    def show_subframe(self, name, frameclass, **opts):
        shown = False
        if name in self.subframes:
            try:
                self.subframes[name].Raise()
                shown = True
            except:
                del self.subframes[name]
        if not shown:
            self.subframes[name] = frameclass(self, **opts)

    def onSelectColumns(self, event=None):
        dgroup = self.controller.get_group()
        self.show_subframe('readfile', ColumnDataFileFrame,
                           group=dgroup.raw,
                           last_array_sel=self.last_array_sel_col,
                           _larch=self.larch,
                           read_ok_cb=partial(self.onRead_OK,
                                              overwrite=True))

    def onCIFBrowse(self, event=None):
        self.show_subframe('cif_feff', CIFFrame, _larch=self.larch)

    def onFeffBrowse(self, event=None):
        self.show_subframe('feff_paths', FeffResultsFrame,
                           xasmain=self, _larch=self.larch)

    def onLoadFitResult(self, event=None):
        pass
        # print("onLoadFitResult??")
        # self.nb.SetSelection(1)
        # self.nb_panels[1].onLoadFitResult(event=event)

    def onReadDialog(self, event=None):
        dlg = wx.FileDialog(self, message="Read Data File",
                            defaultDir=get_cwd(),
                            wildcard=FILE_WILDCARDS,
                            style=wx.FD_OPEN|wx.FD_MULTIPLE)
        self.paths2read = []
        if dlg.ShowModal() == wx.ID_OK:
            self.paths2read = dlg.GetPaths()
        dlg.Destroy()

        if len(self.paths2read) < 1:
            return

        path = self.paths2read.pop(0)
        path = path.replace('\\', '/')
        do_read = True
        if path in self.controller.file_groups:
            do_read = (wx.ID_YES == Popup(self,
                                          "Re-read file '%s'?" % path,
                                          'Re-read file?'))
        if do_read:
            self.onRead(path)

    def onRead(self, path):
        filedir, filename = os.path.split(os.path.abspath(path))
        if self.controller.get_config('chdir_on_fileopen'):
            os.chdir(filedir)
            self.controller.set_workdir()

        # check for athena projects
        if is_athena_project(path):
            self.show_subframe('athena_import', AthenaImporter,
                               filename=path,
                               _larch=self.controller.larch,
                               read_ok_cb=self.onReadAthenaProject_OK)
        # check for Spec File
        elif is_specfile(path):
            self.show_subframe('spec_import', SpecfileImporter,
                               filename=path,
                               _larch=self.larch_buffer.larchshell,
                               last_array_sel=self.last_array_sel_spec,
                               read_ok_cb=self.onReadSpecfile_OK)
        # default to Column File
        else:
            self.show_subframe('readfile', ColumnDataFileFrame,
                               filename=path,
                               _larch=self.larch_buffer.larchshell,
                               last_array_sel = self.last_array_sel_col,
                               read_ok_cb=self.onRead_OK)

    def onReadSpecfile_OK(self, script, path, scanlist, array_sel=None, extra_sums=None):
        """read groups from a list of scans from a specfile"""
        self.larch.eval("_specfile = specfile('{path:s}')".format(path=path))
        dgroup = None
        _path, fname = os.path.split(path)
        first_group = None
        cur_panel = self.nb.GetCurrentPage()
        cur_panel.skip_plotting = True
        symtable = self.larch.symtable
        if array_sel is not None:
            self.last_array_sel_spec = array_sel

        for scan in scanlist:
            gname = fix_varname("{:s}{:s}".format(fname[:6], scan))
            if hasattr(symtable, gname):
                count, tname = 0, gname
                while count < 1e7 and self.larch.symtable.has_group(tname):
                    tname = gname + make_hashkey(length=7)
                    count += 1
                gname = tname

            cur_panel.skip_plotting = (scan == scanlist[-1])
            yname = self.last_array_sel_spec['yarr1']
            if first_group is None:
                first_group = gname
            self.larch.eval(script.format(group=gname, path=path, scan=scan))
            print("SPEC FILE ", yname)
            displayname = f"{fname:s} scan{scan:s} {yname:s}"
            jrnl = {'source_desc': f"{fname:s}: scan{scan:s} {yname:s}"}
            dgroup = self.install_group(gname, displayname,
                                        process=True, plot=False, extra_sums=extra_sums,
                                        source=displayname,
                                        journal=jrnl)
        cur_panel.skip_plotting = False

        if first_group is not None:
            self.ShowFile(groupname=first_group, process=True, plot=True)
        self.write_message("read %d datasets from %s" % (len(scanlist), path))


    def onReadAthenaProject_OK(self, path, namelist, extra_sums=None):
        """read groups from a list of groups from an athena project file"""
        self.larch.eval("_prj = read_athena('{path:s}', do_fft=False, do_bkg=False)".format(path=path))
        dgroup = None
        script = "{group:s} = extract_athenagroup(_prj.{prjgroup:s})"
        cur_panel = self.nb.GetCurrentPage()
        cur_panel.skip_plotting = True
        parent, spath = os.path.split(path)
        labels = []
        groups_added = []
        for gname in namelist:
            cur_panel.skip_plotting = (gname == namelist[-1])
            this = getattr(self.larch.symtable._prj, gname)
            gid = str(getattr(this, 'athena_id', gname))
            if self.larch.symtable.has_group(gid):
                count, prefix = 0, gname[:3]
                while count < 1e7 and self.larch.symtable.has_group(gid):
                    gid = prefix + make_hashkey(length=7)
                    count += 1
            label = getattr(this, 'label', gname).strip()
            labels.append(label)

            jrnl = {'source_desc': f'{spath:s}: {gname:s}'}
            self.larch.eval(script.format(group=gid, prjgroup=gname))
            dgroup = self.install_group(gid, label, process=True, plot=False,
                                        source=path, journal=jrnl,
                                        extra_sums=extra_sums)
            groups_added.append(gid)

        for gid in groups_added:
            rgroup = gid
            dgroup = self.larch.symtable.get_group(gid)
            apars = getattr(dgroup, 'athena_params', {})
            refgroup = getattr(apars, 'reference', '')
            # print("# looking for refgroup for ", gid, dgroup.filename)
            if refgroup in groups_added:
                newname = None
                for key, val in self.controller.file_groups.items():
                    if refgroup in (key, val):
                        newname = key

                if newname is not None:
                    refgroup = newname
            else:
                refgroup = dgroup.filename
            # print("# final eref to ", refgroup)
            dgroup.energy_ref = refgroup

        self.larch.eval("del _prj")
        cur_panel.skip_plotting = False

        plot_first = True
        if len(labels) > 0:
            gname = self.controller.file_groups[labels[0]]
            self.ShowFile(groupname=gname, process=True, plot=plot_first)
            plot_first = False
        self.write_message("read %d datasets from %s" % (len(namelist), path))
        self.last_project_file = path

    def onRead_OK(self, script, path, groupname=None, filename=None,
                  ref_groupname=None, ref_filename=None,
                  array_sel=None, overwrite=False, extra_sums=None, array_desc=None):
        """ called when column data has been selected and is ready to be used
        overwrite: whether to overwrite the current datagroup, as when
        editing a datagroup
        """
        if groupname is None:
            return
        if array_desc is None:
            array_desc = {}

        abort_read = False
        filedir, spath = os.path.split(path)
        if filename is None:
            filename = spath
        if not overwrite and hasattr(self.larch.symtable, groupname):
            groupname = file2groupname(spath, symtable=self.larch.symtable)

        if abort_read:
            return

        self.larch.eval(script.format(group=groupname, path=path,
                                      refgroup=ref_groupname))
        if array_sel is not None:
            self.last_array_sel_col = array_sel

        journal = {'source': path}
        refjournal = {}

        if 'xdat' in array_desc:
            journal['xdat'] = array_desc['xdat'].format(group=groupname)
        if 'ydat' in array_desc:
            journal['ydat'] = ydx = array_desc['ydat'].format(group=groupname)
            journal['source_desc'] = f'{spath:s}: {ydx:s}'
        if 'yerr' in array_desc:
            journal['yerr'] = array_desc['yerr'].format(group=groupname)

        self.install_group(groupname, filename, overwrite=overwrite,
                           extra_sums=extra_sums, source=path, journal=journal)

        if ref_groupname is not None:
            if 'xdat' in array_desc:
                refjournal['xdat'] = array_desc['xdat'].format(group=groupname)
            if 'yref' in array_desc:
                refjournal['ydat'] = ydx = array_desc['yref'].format(group=groupname)
                refjournal['source_desc'] = f'{spath:s}: {ydx:s}'
            self.install_group(ref_groupname, ref_filename,
                               overwrite=overwrite,
                               extra_sums=extra_sums,
                               source=path, journal=journal)

        # check if rebin is needed
        thisgroup = getattr(self.larch.symtable, groupname)

        do_rebin = False
        if thisgroup.datatype == 'xas':
            try:
                en = thisgroup.energy
            except:
                do_rebin = True
                en = thisgroup.energy = thisgroup.xdat
            # test for rebinning:
            #  too many data points
            #  unsorted energy data or data in angle
            #  too fine a step size at the end of the data range
            if (len(en) > 1200 or
                any(np.diff(en) < 0) or
                ((max(en)-min(en)) > 300 and
                 (np.diff(en[-50:]).mean() < 0.75))):
                msg = """This dataset may need to be rebinned.
                Rebin now?"""
                dlg = wx.MessageDialog(self, msg, 'Warning',
                                       wx.YES | wx.NO )
                do_rebin = (wx.ID_YES == dlg.ShowModal())
                dlg.Destroy()

        for path in self.paths2read:
            path = path.replace('\\', '/')
            filedir, spath = os.path.split(path)
            gname = file2groupname(spath, symtable=self.larch.symtable)
            ref_gname = ref_fname = None
            if ref_groupname is not None:
                ref_gname = gname + '_ref'
                ref_fname = spath + '_ref'
            self.larch.eval(script.format(group=gname, refgroup=ref_gname,
                                          path=path))
            if 'xdat' in array_desc:
                journal['xdat'] = array_desc['xdat'].format(group=gname)
            if 'ydat' in array_desc:
                journal['ydat'] = ydx = array_desc['ydat'].format(group=gname)
                journal['source_desc'] = f'{spath:s}: {ydx:s}'
            if 'yerr' in array_desc:
                journal['yerr'] = array_desc['yerr'].format(group=gname)

            self.install_group(gname, spath, overwrite=overwrite,
                               source=path, journal=journal)
            if ref_gname is not None:
                if 'xdat' in array_desc:
                    refjournal['xdat'] = array_desc['xdat'].format(group=ref_gname)
                if 'yref' in array_desc:
                    refjournal['ydat'] = ydx = array_desc['yref'].format(group=ref_gname)
                    refjournal['source_desc'] = f'{spath:s}: {ydx:s}'
                self.install_group(ref_gname, ref_fname, overwrite=overwrite,
                               source=path, journal=ref_journal)


        self.write_message("read %s" % (spath))

        if do_rebin:
            RebinDataDialog(self, self.controller).Show()

    def install_group(self, groupname, filename, overwrite=False,
                      process=True, rebin=False, plot=True, extra_sums=None,
                      source=None, journal=None):
        """add groupname / filename to list of available data groups"""

        try:
            thisgroup = getattr(self.larch.symtable, groupname)
        except AttributeError:
            thisgroup = self.larch.symtable.new_group(groupname)

        datatype = getattr(thisgroup, 'datatype', 'raw')
        # file /group may already exist in list
        if filename in self.controller.file_groups and not overwrite:
            fbase, i = filename, 0
            while i < 10000 and filename in self.controller.file_groups:
                filename = "%s_%d" % (fbase, i)
                i += 1

        if source is None:
            source = filename

        _j =  {'source': f"{source}"}
        _j.update(journal)
        jopts = ', '.join([f"{k}='{v}'" for k, v in _j.items()])

        cmds = [f"{groupname:s}.groupname = '{groupname:s}'",
                f"{groupname:s}.filename = '{filename:s}'",
                f"{groupname:s}.journal = journal({jopts:s})"]
        if datatype == 'xas':
            cmds.append(f"{groupname:s}.energy_orig = {groupname:s}.energy[:]")
        cmds = ('\n'.join(cmds))

        if extra_sums is not None:
            self.extra_sums = extra_sums
            # print("## need to handle extra_sums " , self.extra_sums)

        self.larch.eval(cmds)

        self.controller.filelist.Append(filename.strip())
        self.controller.file_groups[filename] = groupname

        self.nb.SetSelection(0)
        self.ShowFile(groupname=groupname, filename=filename,
                      process=process, plot=plot)
        self.controller.filelist.SetStringSelection(filename.strip())
        # self.get_nbpage('xasnorm')[1].process(dgroup, force=force)
        return thisgroup

    ##
    ## float-spin / pin timer events
    def onPinTimer(self, event=None):
        if 'start' not in self.cursor_dat:
            self.cursor_dat['xsel'] = None
            self.onPinTimerComplete(reason="bad")
        pin_config = self.controller.get_config('pin_config',
                                                {'style': 'pin_first',
                                                 'timeout':15.0,
                                                 'min_time': 2.0})
        min_time  = float(pin_config['min_time'])
        timeout = float(pin_config['timeout'])

        curhist_name = self.cursor_dat['name']
        cursor_hist = getattr(self.larch.symtable._plotter, curhist_name, [])
        if len(cursor_hist) > self.cursor_dat['nhist']: # got new data!
            self.cursor_dat['xsel'] = cursor_hist[0][0]
            self.cursor_dat['ysel'] = cursor_hist[0][1]
            if time.time() > min_time + self.cursor_dat['start']:
                self.timers['pin'].Stop()
                self.onPinTimerComplete(reason="new")
        elif time.time() > timeout + self.cursor_dat['start']:
            self.onPinTimerComplete(reason="timeout")

        if 'win' in self.cursor_dat and 'xsel' in self.cursor_dat:
            time_remaining = timeout + self.cursor_dat['start'] - time.time()
            msg = 'Select Point from Plot #%d' % (self.cursor_dat['win'])
            if self.cursor_dat['xsel'] is not None:
                msg = '%s, [current value=%.1f]' % (msg, self.cursor_dat['xsel'])
            msg = '%s, expiring in %.0f sec' % (msg, time_remaining)
            self.write_message(msg)

    def onPinTimerComplete(self, reason=None, **kws):
        self.timers['pin'].Stop()
        if reason != "bad":
            msg = 'Selected Point at %.1f' % self.cursor_dat['xsel']
            if reason == 'timeout':
                msg = msg + '(timed-out)'
            self.write_message(msg)
            if (self.cursor_dat['xsel'] is not None and
                callable(self.cursor_dat['callback'])):
                self.cursor_dat['callback'](**self.cursor_dat)
                time.sleep(0.05)
        else:
            self.write_message('Select Point Error')
        self.cursor_dat = {}


    def onSelPoint(self, evt=None, opt='__', relative_e0=True, callback=None,
                   win=None):
        """
        get last selected point from a specified plot window
        and fill in the value for the widget defined by `opt`.

        start Pin Timer to get last selected point from a specified plot window
        and fill in the value for the widget defined by `opt`.
        """
        if win is None:
            win = 1
        display = _getDisplay(win=win, _larch=self.larch)
        display.Raise()
        msg = 'Select Point from Plot #%d' % win
        self.write_message(msg)

        now = time.time()
        curhist_name = 'plot%d_cursor_hist' % win
        cursor_hist = getattr(self.larch.symtable._plotter, curhist_name, [])

        self.cursor_dat = dict(relative_e0=relative_e0, opt=opt,
                               callback=callback,
                               start=now, xsel=None, ysel=None,
                               win=win, name=curhist_name,
                               nhist=len(cursor_hist))

        pin_config = self.controller.get_config('pin_config',
                                                {'style': 'pin_first',
                                                 'timeout':15.0,
                                                 'min_time': 2.0})
        if pin_config['style'] == 'plot_first':
            if len(cursor_hist) > 0:
                x, y, t = cursor_hist[0]
                if now < (t + 60.0):
                    self.cursor_dat['xsel'] = x
                    self.cursor_dat['ysel'] = y
                    msg = 'Selected Point at %.1f' % self.cursor_dat['xsel']
                    self.cursor_dat['callback'](**self.cursor_dat)
            else:
                self.write_message('No Points selected from plot window!')
        else: # "pin first" mode
            if len(cursor_hist) > 2:  # purge old cursor history
                setattr(self.larch.symtable._plotter, curhist_name, cursor_hist[:2])

            if len(cursor_hist) > 0:
                x, y, t = cursor_hist[0]
                if now < (t + 30.0):
                    self.cursor_dat['xsel'] = x
                    self.cursor_dat['ysel'] = y
            self.timers['pin'].Start(250)


class XASViewer(LarchWxApp):
    def __init__(self, filename=None, **kws):
        self.filename = filename
        LarchWxApp.__init__(self, **kws)

    def createApp(self):
        frame = XASFrame(filename=self.filename,
                         version_info=self.version_info)
        self.SetTopWindow(frame)
        return True


def xas_viewer(**kws):
    XASViewer(**kws)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Larch XAS GUI")
    parser.add_argument(
        '-f', '--filename',
        dest='filename',
        help='data file to load')
    args = parser.parse_args()
    app = XASViewer(**vars(args))
    app.MainLoop()
