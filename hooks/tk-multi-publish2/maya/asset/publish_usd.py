﻿# Copyright (c) 2017 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import copy
import pprint
import maya.cmds as cmds
import maya.mel as mel
from pxr import Kind, Sdf, Usd, UsdGeom
import sgtk
from tank_vendor import six

HookBaseClass = sgtk.get_hook_baseclass()


class MayaSessionUSDPublishPlugin(HookBaseClass):
    """
    Plugin for publishing an open maya session.

    This hook relies on functionality found in the base file publisher hook in
    the publish2 app and should inherit from it in the configuration. The hook
    setting for this plugin should look something like this::

        hook: "{self}/publish_file.py:{engine}/tk-multi-publish2/basic/publish_session.py"

    """

    # NOTE: The plugin icon and name are defined by the base file plugin.

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """

        return """
        <p>This plugin publishes session geometry for the current session. Any
        session geometry will be exported to the path defined by this plugin's
        configured "Publish Template" setting. The plugin will fail to validate
        if the "AbcExport" plugin is not enabled or cannot be found.</p>
        """

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to receive
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form::

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts as
        part of its environment configuration.
        """
        # inherit the settings from the base publish plugin
        base_settings = super(MayaSessionUSDPublishPlugin, self).settings or {}

        # settings specific to this class
        maya_publish_settings = {
            "Publish Template": {
                "type": "template",
                "default": None,
                "description": "Template path for published work files. Should"
                               "correspond to a template defined in "
                               "templates.yml.",
            }
        }

        # update the base settings
        base_settings.update(maya_publish_settings)


        file_type = {
            "File Types": {
                "type": "list",
                "default": [
                    ["Component USD", "usd"],
                ],
                "description": (
                    "List of file types to include. Each entry in the list "
                    "is a list in which the first entry is the Shotgun "
                    "published file type and subsequent entries are file "
                    "extensions that should be associated."
                )
            },
        }

        base_settings.update(file_type)
        
        return base_settings

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for example
        ["maya.*", "file.maya"]
        """
        return ["maya.session.component.usd"]

    def accept(self, settings, item):
        """
        Method called by the publisher to determine if an item is of any
        interest to this plugin. Only items matching the filters defined via the
        item_filters property will be presented to this method.

        A publish task will be generated for each item accepted here. Returns a
        dictionary with the following booleans:

            - accepted: Indicates if the plugin is interested in this value at
                all. Required.
            - enabled: If True, the plugin will be enabled in the UI, otherwise
                it will be disabled. Optional, True by default.
            - visible: If True, the plugin will be visible in the UI, otherwise
                it will be hidden. Optional, True by default.
            - checked: If True, the plugin will be checked in the UI, otherwise
                it will be unchecked. Optional, True by default.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process

        :returns: dictionary with boolean keys accepted, required and enabled
        """

        accepted = True
        publisher = self.parent
        template_name = settings["Publish Template"].value

        # ensure a work file template is available on the parent item
        work_template = item.parent.properties.get("work_template")
        if not work_template:
            self.logger.debug(
                "A work template is required for the session item in order to "
                "publish session geometry. Not accepting session geom item."
            )
            accepted = False

        # ensure the publish template is defined and valid and that we also have
        publish_template = publisher.get_template_by_name(template_name)
        if not publish_template:
            self.logger.debug(
                "The valid publish template could not be determined for the "
                "session geometry item. Not accepting the item."
            )
            accepted = False

        # we've validated the publish template. add it to the item properties
        # for use in subsequent methods
        item.properties["publish_template"] = publish_template

        if cmds.about(version=1) == "2022":
            if not mel.eval("exists \"mayaUSDExport\""):
                plugin_load = cmds.loadPlugin("mayaUsdPlugin.so")
        else:
            if not mel.eval("exists \"usdExport\""):
                plugin_load = cmds.loadPlugin("pxrUsd.so")
            #self.logger.debug(
            #    "Item not accepted because alembic export command 'usdExport' "
            #    "is not available. Perhaps the plugin is not enabled?"
            #)
            #accepted = False

        # because a publish template is configured, disable context change. This
        # is a temporary measure until the publisher handles context switching
        # natively.
        item.context_change_allowed = False

        return {
            "accepted": accepted,
            "checked": True
        }

    def validate(self, settings, item):
        """
        Validates the given item to check that it is ok to publish. Returns a
        boolean to indicate validity.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        :returns: True if item is valid, False otherwise.
        """

        path = _session_path()
        
        

        # ---- ensure the session has been saved

        if not path:
            # the session still requires saving. provide a save button.
            # validation fails.
            error_msg = "The Maya session has not been saved."
            self.logger.error(
                error_msg,
                extra=_get_save_as_action()
            )
            raise Exception(error_msg)

        # get the normalized path
        path = sgtk.util.ShotgunPath.normalize(path)

        check_component = cmds.ls(item.context.entity['name'])

        if not check_component or not len(check_component) == 1:
            error_msg = "Componen is not."
            self.logger.error(
                error_msg,
            )
            raise Exception(error_msg)


        # get the configured work file template
        work_template = item.parent.properties.get("work_template")
        publish_template = item.properties.get("publish_template")

        # get the current scene path and extract fields from it using the work
        # template:
        work_fields = work_template.get_fields(path)

        # ensure the fields work for the publish template
        missing_keys = publish_template.missing_keys(work_fields)
        if missing_keys:
            error_msg = "Work file '%s' missing keys required for the " \
                        "publish template: %s" % (path, missing_keys)
            self.logger.error(error_msg)
            raise Exception(error_msg)

        # create the publish path by applying the fields. store it in the item's
        # properties. This is the path we'll create and then publish in the base
        # publish plugin. Also set the publish_path to be explicit.
        item.properties["path"] = publish_template.apply_fields(work_fields)
        item.properties["publish_path"] = item.properties["path"]


        # use the work file's version number when publishing
        if "version" in work_fields:
            item.properties["publish_version"] = work_fields["version"]

        # run the base class validation
        return super(MayaSessionUSDPublishPlugin, self).validate(
            settings, item)

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
            the keys returned in the settings property. The values are `Setting`
            instances.
        :param item: Item to process
        """
        #self._export_wwusd()



        usdexport_command = "mayaUSDExport" if cmds.about(version=1)=="2022"  else "usdExport"     
        publisher = self.parent

        # get the path to create and publish
        publish_path = item.properties["path"]

        # ensure the publish folder exists:
        publish_folder = os.path.dirname(publish_path)
        self.parent.ensure_folder_exists(publish_folder)
        self._append_mesh_attr_usd()

        # set the alembic args that make the most sense when working with Mari.
        # These flags will ensure the export of an USD file that contains
        # all visible geometry from the current scene together with UV's and
        # face sets for use in Mari.


        
        usd_args = [
            '-shd "none"',
            '-dms "none"',
            '-uvs 1',
            '-cls 0',
            '-vis 0',
            '-mt 0',
            '-sl'
            ]



        # find the animated frame range to use:
        start_frame, end_frame = _find_scene_animation_range()
        if start_frame and end_frame:
            usd_args.append("-fr %d %d" % (start_frame, end_frame))

        # Set the output path: 
        # Note: The AbcExport command expects forward slashes!

        #usd_args.append('-f "%s"' % publish_path.replace("\\", "/"))

        # build the export command.  Note, use AbcExport -help in Maya for
        # more detailed USD export help
        sub_components = [ x for x in cmds.ls(allPaths=1,ca=0,transforms=1,l=1) 
        if cmds.listRelatives(x,p=1) 
        and cmds.attributeQuery("Meshtype",node=x,exists=1) 
        and cmds.getAttr(x+".Meshtype", asString=True) == "component"]
        
        if not sub_components:
            
            usd_args.append('-f "%s"' % publish_path.replace("\\", "/"))
            usd_export_cmd = (usdexport_command + " ".join(usd_args))
            cmds.select(item.properties['name'])
            mel.eval(usd_export_cmd)
            
            super(MayaSessionUSDPublishPlugin, self).publish(settings, item)
            return

        asset_usd_path = self._get_sub_component_path(item.properties['name'],item)
        self.parent.ensure_folder_exists(os.path.dirname(asset_usd_path))
        usd_args.append('-f "%s"' % asset_usd_path.replace("\\", "/"))
        usd_export_cmd = (usdexport_command + " ".join(usd_args))
        cmds.select(item.properties['name'])
        mel.eval(usd_export_cmd)

        sub_component_parents = list(set([cmds.listRelatives(x,p=1,f=1)[0] for x in sub_components])) 

        root_layer =  Sdf.Layer.CreateNew(publish_path, args = {'format':'usda'})
        component_stage = Usd.Stage.Open(root_layer)
        component_prim = UsdGeom.Xform.Define(component_stage,"/%s"%item.properties['name']).GetPrim()
        component_stage.SetDefaultPrim(component_prim)
        UsdGeom.SetStageUpAxis(component_stage, UsdGeom.Tokens.y)
        model = Usd.ModelAPI(component_prim)
        model.SetKind(Kind.Tokens.assembly)

        component_prim.GetReferences().AddReference(self._get_relatives_path(publish_path,asset_usd_path).replace("\\","/"))
        



        sub_component_parents = self._return_order_node_list(sub_component_parents)
        sub_component_parents.reverse()
        for parent in sub_component_parents:

            child_prim = UsdGeom.Xform.Define(component_stage,parent.replace("|","/")).GetPrim()
            model = Usd.ModelAPI(child_prim)
            model.SetKind(Kind.Tokens.assembly)
            #self._set_xform(parent,child_prim)
        sub_components = self._return_order_node_list(sub_components)
        

        for sub_component in sub_components:
            

            #sub_component_path = self._get_sub_component_path(sub_component,item)
            #sub_usd_args = usd_args
            #sub_usd_args.append('-f "%s"' % sub_component_path.replace("\\", "/"))
            #usd_export_cmd = ("usdExport %s" % " ".join(sub_usd_args))
            #sub_usd_args.pop(-1)
            #cmds.select(sub_component)
            #mel.eval(usd_export_cmd)

            #child_prim = component_stage.OverridePrim(sub_component.replace("|","/"))
            child_prim = UsdGeom.Xform.Define(component_stage,sub_component.replace("|","/")).GetPrim()
            model = Usd.ModelAPI(child_prim)
            model.SetKind(Kind.Tokens.component)
            child_prim.SetInstanceable(1)
            #component_prim.GetReferences().AddReference(sub_component_path)
            #self._set_xform(sub_component,child_prim)

        # ...and execute it:
        try:
            self.parent.log_debug("Executing command: %s" % usd_export_cmd)
            status = component_stage.GetRootLayer().Save()
        except Exception as e:
            import traceback
            
            self.parent.log_debug("Executing command: %s" % usd_export_cmd)
            self.logger.error("Failed to export USD: %s"% e, 
                extra = {
                "action_show_more_info": {
                    "label": "Error Details",
                    "tooltip": "Show the full error stack trace",
                    "text": "<pre>%s</pre>" % (traceback.format_exc(),)
                }
            }

            )
            return

        # Now that the path has been generated, hand it off to the

        self._export_wwusd()

        super(MayaSessionUSDPublishPlugin, self).publish(settings, item)
    

    def _export_wwusd(self):

        #from WWUSD_MAYA import  Model

#        import sys
#        sys.path.append( '/home/w10137/work/ww_usd/WWUSD_MAYA' )
        import sys
        if 'linux' not in sys.platform:
            sys.path.append( '\\\\10.0.40.42\\inhouse\\tool\\rez-packages\\ww_usd\\1.0.0\\ww_usd'  )
            # sys.path.append( '\\\\10.0.40.42\\user\\pipeline\\kyoungmin\\work'  )

        from WWUSD_MAYA import export_asset
        import imp
        imp.reload( export_asset )


        current_engine = sgtk.platform.current_engine()
        tk = current_engine.sgtk
        sg = tk.shotgun
        context = current_engine.context
        template = tk.templates["usd_asset_root"]

        path = cmds.file(query=True, sn=True)

        if path is not None:
            path = six.ensure_str(path)
        else: 
            return 
        
        work_template = tk.templates["maya_asset_work"]
        work_fields = work_template.get_fields(path)
        usd_asset_root_template = tk.templates["usd_asset_root"]
        root_path = usd_asset_root_template.apply_fields(work_fields)
        usd_asset_step_template = tk.templates['usd_asset_step']
        asset_step_path = usd_asset_step_template.apply_fields(work_fields)


        usd_asset_step_ver_template = tk.templates['usd_asset_step_version']
        asset_step_ver_path = usd_asset_step_ver_template.apply_fields(work_fields)

        key = [['code', 'is', 'Component USD']]
        file_type = sg.find_one("PublishedFileType", key, ['id','code'])
        
        def _chcek_publish(publish_path,version):
            
            key = [
                    ['project', 'is', context.project],
                    ['entity', 'is', context.entity],
                    ["published_file_type", "is", file_type],
                    ['name', 'is', os.path.basename(publish_path)],
                    ['version_number', 'is',version]
                    ]
            published_ent = sg.find_one("PublishedFile", key, ['version_number'])
            return not published_ent 

        def _publish_to_sg(publish_path,version):

            publish_data = {
                            "tk": tk,
                            "context": context,
                            "path": publish_path,
                            "name": os.path.basename(publish_path),
                            "created_by": context.user,
                            "version_number": version,
                            "published_file_type": file_type['code'],
                        }

            sgtk.util.register_publish(**publish_data)
        
        def _create_usd_library(publish_path):
            
            project = {"type":"Project","id":884}
            key = [
                    ['project','is',project],
                    ['code','is',os.path.basename(publish_path)],
                    ['sg_project_name','is',context.project['name']]
                ]
            usd_lib_ent = sg.find_one("CustomEntity06",key)

            if usd_lib_ent:
                return

            url = {
                    'content_type': "string",
                    'link_type': "local" ,
                    'name': os.path.basename(publish_path),
                    'local_path':os.path.dirname(publish_path),
                    'url': "string"}

            desc = {"project":project,
                        'code':os.path.basename(publish_path),
                        'sg_path':url,
                        'sg_project_name':context.project['name']}
    
            sg.create("CustomEntity06",desc)
                



        if _chcek_publish(root_path,1):
            _publish_to_sg(root_path,1)

#        if _chcek_publish(asset_step_path,1):
#            _publish_to_sg(asset_step_path,1)

#        if _chcek_publish(asset_step_ver_path,work_fields['version']):
#                _publish_to_sg(asset_step_ver_path,work_fields['version'])


        export_args = {
            "project" :  context.project['name'],
            "asset_type" : work_fields['sg_asset_type'],
            "asset" :  work_fields['Asset'],
            "dept" :  work_fields["Step"],
            "name" : work_fields['Asset'],
            "ver" : work_fields['version']
        }
        if not os.path.exists(asset_step_ver_path):
            export_asset.export_asset( export_args['asset'] )
            #Model.export(export_args,os.path.dirname(root_path))
        _create_usd_library(root_path)


    

    def _get_relatives_path(self,publish_path,asset_usd_path):
        common_prefix = os.path.commonprefix([os.path.dirname(publish_path),asset_usd_path])
        return os.path.relpath(asset_usd_path, common_prefix)
    
    def _return_order_node_list(self,node_list):
        
        return_list = []

        parents = list(set([cmds.listRelatives(x,p=1,f=1)[0] for x in node_list if cmds.listRelatives(x,p=1)])) 
        for parent in parents:
            nodes = cmds.listRelatives(parent,c=1,f=1)
            nodes.reverse()
            return_list.extend(nodes)
            #return_list.extend(cmds.listRelatives(parent,c=1,f=1))
        
        return return_list
        
    
    def _get_sub_component_path(self,sub_component,item):
        path = os.path.splitext(item.properties["path"])[0]
        path = os.path.join(path,sub_component.replace("|","_")+'.usd')
        
        return path

    def _set_xform(self,node,prim):

        translate = cmds.xform(node,q=1,t=1)
        rotate = cmds.xform(node,q=1,ro=1)
        scale = cmds.xform(node,q=1,s=1)

        xformAPI = UsdGeom.XformCommonAPI(prim)
        xformAPI.SetTranslate(translate)
        xformAPI.SetRotate(rotate)
        xformAPI.SetScale(scale)
       

    def _append_mesh_attr_usd(self):
        import sys
        from collections import OrderedDict
        import json
        try:
            meshes = cmds.ls(typ="mesh")
            for mesh in meshes:


                mesh_attributes = OrderedDict()
                if cmds.listAttr(mesh,ud=1):
                    for meshTag in cmds.listAttr(mesh, ud=True):
                        if meshTag in  ["Meshtype","USD_UserExportedAttributesJson"] :
                            continue
                        elif meshTag in ["MtlTag","Doubleside","Subdivision","Displace"]:
                            mesh_attributes[meshTag] = cmds.getAttr("%s.%s" % (mesh, meshTag), asString=True)
            
                    if mesh_attributes:
                        if cmds.attributeQuery("USD_UserExportedAttributesJson", node = mesh, exists=True):
                            cmds.setAttr(mesh + ".USD_UserExportedAttributesJson", l=False)
                        else:
                            cmds.addAttr(mesh,ln="USD_UserExportedAttributesJson",dt="string")
                        usd_attr = json.dumps(mesh_attributes, ensure_ascii=False, indent=4)
                        cmds.setAttr(mesh + ".USD_UserExportedAttributesJson", usd_attr, type="string")
                        cmds.setAttr(mesh + ".USD_UserExportedAttributesJson", l=True)
        except Exception as e:
            _, _ , tb = sys.exc_info() 
            print ('file name = ', __file__ )
            print ('error line No = {}'.format(tb.tb_lineno))
            print (e)
            raise Exception("Failed to atnt mesh tag  <br> Detail :%s"%e)





def _find_scene_animation_range():
    """
    Find the animation range from the current scene.
    """
    # look for any animation in the scene:
    animation_curves = cmds.ls(typ="animCurve")

    # if there aren't any animation curves then just return
    # a single frame:
    if not animation_curves:
        return 1, 1

    # something in the scene is animated so return the
    # current timeline.  This could be extended if needed
    # to calculate the frame range of the animated curves.
    start = int(cmds.playbackOptions(q=True, min=True))
    end = int(cmds.playbackOptions(q=True, max=True))

    return start, end


def _session_path():
    """
    Return the path to the current session
    :return:
    """
    path = cmds.file(query=True, sn=True)

    if path is not None:
        path = six.ensure_str(path)

    return path


def _get_save_as_action():
    """
    Simple helper for returning a log action dict for saving the session
    """

    engine = sgtk.platform.current_engine()

    # default save callback
    callback = cmds.SaveScene

    # if workfiles2 is configured, use that for file save
    if "tk-multi-workfiles2" in engine.apps:
        app = engine.apps["tk-multi-workfiles2"]
        if hasattr(app, "show_file_save_dlg"):
            callback = app.show_file_save_dlg

    return {
        "action_button": {
            "label": "Save As...",
            "tooltip": "Save the current session",
            "callback": callback
        }
    }
