#!/usr/bin/env python

#
# Copyright 2020 Autodesk
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import unittest
import tempfile
import testUtils
from os import path
from maya import cmds, mel
import mayaUsd_createStageWithNewLayer
import mayaUsd
from mayaUsd import ufe as mayaUsdUfe

from pxr import Sdf, Usd

CLEAR = "-clear"
DISCARD = "-discardEdits"

DUMMY_FILE_TEXT = \
    """#usda 1.0
def Sphere "ball"
    {
        custom string Winner = "shotFX"
    }
"""


def getCleanMayaStage():
    """ gets a stage that only has an anon layer """

    cmds.file(new=True, force=True)
    shapePath = mayaUsd_createStageWithNewLayer.createStageWithNewLayer()
    stage = mayaUsd.lib.GetPrim(shapePath).GetStage()
    return shapePath, stage


class MayaUsdLayerEditorCommandsTestCase(unittest.TestCase):
    """ test mel commands intended for the layer editor """

    @classmethod
    def setUpClass(cls):
        cmds.loadPlugin('mayaUsdPlugin')

    def testEditTarget(self):
        """ tests 'mayaUsdEditTarget' command, but also touches on adding anon layers """
        shapePath, stage = getCleanMayaStage()

        # original target is the root anonymous layer
        originalTargetID = stage.GetEditTarget().GetLayer().identifier
        rootLayer = stage.GetRootLayer()
        self.assertEqual(originalTargetID, rootLayer.identifier)

        currentTargetID = cmds.mayaUsdEditTarget(shapePath, query=True, editTarget=True)[0]
        self.assertEqual(originalTargetID, currentTargetID)

        # add two sub layers
        redLayerId = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="redLayer")[0]
        greenLayerId = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="greenLayer")[0]
        self.assertEqual(len(rootLayer.subLayerPaths), 2)

        cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=greenLayerId)
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, greenLayerId)
        # do we return that new target?
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier,
                         cmds.mayaUsdEditTarget(shapePath, query=True, editTarget=True)[0])
        undoStepTwo = greenLayerId

        cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=redLayerId)
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, redLayerId)
        undoStepOne = redLayerId

        # can we set the target back to the root?
        rootLayerID = rootLayer.identifier
        cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=rootLayerID)
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)

        # undo
        cmds.undo()
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, undoStepOne)
        cmds.undo()
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, undoStepTwo)

        # test removing the edit target
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, greenLayerId)
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[0,shapePath])
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)
        cmds.undo()
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, greenLayerId)

        cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=redLayerId)

        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, redLayerId)
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[1,shapePath])
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)
        cmds.undo()
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, redLayerId)

        # test bad input
        with self.assertRaises(RuntimeError):
            cmds.mayaUsdEditTarget("bogusShape", query=True, editTarget=True)

        with self.assertRaises(RuntimeError):
            cmds.mayaUsdEditTarget("bogusShape", edit=True, editTarget=greenLayerId)

        with self.assertRaises(RuntimeError):
            cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget="bogusLayer")

    def testSubLayerEditing(self):
        """ test 'mayaUsdLayerEditor' command """
        _shapePath, stage = getCleanMayaStage()
        rootLayer = stage.GetRootLayer()

        # -addAnonymous

        # add three layers. Note: these are always added at the top
        layer3Id = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="Layer1")[0]
        layer2Id = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="Layer2")[0]
        layer1Id = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="Layer3")[0]

        # check that they were added
        addedLayers = [layer1Id, layer2Id, layer3Id]
        self.assertEqual(rootLayer.subLayerPaths, addedLayers)

        # test undo
        cmds.undo() # undo last add
        self.assertEqual(len(rootLayer.subLayerPaths), 2)
        cmds.undo()
        cmds.undo() # all gone now
        self.assertEqual(len(rootLayer.subLayerPaths), 0)
        # put them all back
        for _ in range(3):
            cmds.redo()
        self.assertEqual(rootLayer.subLayerPaths, addedLayers)
        # remove them again
        for _ in range(3):
            cmds.undo()
        # redo  again
        for _ in range(3):
            cmds.redo()
        # all back?
        self.assertEqual(rootLayer.subLayerPaths, addedLayers)

        # -removeSubPath
        # remove second sublayer
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[1,_shapePath])
        afterDeletion = [layer1Id, layer3Id]
        self.assertEqual(rootLayer.subLayerPaths, afterDeletion)

        # remove second sublayer again to leave only one
        afterDeletion = [layer1Id, layer3Id]
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[1,_shapePath])
        self.assertEqual(rootLayer.subLayerPaths, [layer1Id])

        # remove second sublayer  -- this time it's out of bounds
        with self.assertRaises(RuntimeError):
            cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[1,_shapePath])

        # undo twice to get back to three layers
        cmds.undo()
        cmds.undo()
        self.assertEqual(rootLayer.subLayerPaths, addedLayers)
        # redo deletion of second layer
        cmds.redo()
        self.assertEqual(rootLayer.subLayerPaths, afterDeletion)
        cmds.undo()

        # put a sub layer and a sub-sub layer on the top layer.
        childLayerId = cmds.mayaUsdLayerEditor(layer1Id, edit=True, addAnonymous="ChildLayer")[0]
        grandChildLayerId = cmds.mayaUsdLayerEditor(childLayerId, edit=True, addAnonymous="GrandChild")[0]

        # delete the top layer.  See if it comes back after redo.
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[0,_shapePath])
        # check layer1 was deleted
        self.assertEqual(rootLayer.subLayerPaths, [layer2Id, layer3Id])
        # bring it back
        cmds.undo()
        firstLayer = Sdf.Layer.Find(layer1Id)
        self.assertIsNotNone(firstLayer)

        # check the children were not deleted
        self.assertEqual(firstLayer.subLayerPaths[0], childLayerId)
        childLayer = Sdf.Layer.Find(childLayerId)
        self.assertEqual(childLayer.subLayerPaths[0], grandChildLayerId)

        # -insertSubPath

        # insert two layers
        rootLayer.subLayerPaths.clear()
        second = "second.usda"
        first = "first.usda"
        newName = "david.usda"
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[0, second])
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[0, first])
        # message for anyone looking at the test log:
        # It's normal for stage.cpp to print the error that it can't find second.usa and first.usda, they do not exist"
        self.assertEqual(rootLayer.subLayerPaths, [first, second])

        # bad index
        with self.assertRaises(RuntimeError):
            cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[-2, "bogus"])
        with self.assertRaises(RuntimeError):
            cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[3, "bogus"])

        # -replaceSubPath

        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, replaceSubPath=[first, newName])
        self.assertEqual(rootLayer.subLayerPaths, [newName, second])
        # bad replace path
        with self.assertRaises(RuntimeError):
            cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, replaceSubPath=["notfound", newName])

        # -clear
        # -discardEdits

        testPasses = [CLEAR, DISCARD]
        for testPass in testPasses:

            if testPass == CLEAR:
                # get a clear stage
                rootLayer.subLayerPaths.clear()
            if testPass == DISCARD:
                # save and load a stage
                testUsdFile = tempfile.NamedTemporaryFile(suffix=".usda", prefix="dummy", delete=False, mode="w")
                testUsdFile.write(DUMMY_FILE_TEXT)
                testUsdFile.close()

                newStage = Usd.Stage.Open(testUsdFile.name)
                rootLayer = newStage.GetRootLayer()
                self.assertEqual(len(rootLayer.subLayerPaths), 0)

            childLayerId = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="Child")[0]
            grandChildLayerId = cmds.mayaUsdLayerEditor(childLayerId, edit=True, addAnonymous="GrandChild")[0]

            if testPass == CLEAR:
                cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, clear=True)
            if testPass == DISCARD:
                cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, discardEdits=True)

            # test everything is gone
            self.assertEqual(len(rootLayer.subLayerPaths), 0)

            cmds.undo()  # layers are back
            self.assertEqual(len(rootLayer.subLayerPaths), 1)
            self.assertEqual(rootLayer.subLayerPaths[0], childLayerId)
            cmds.redo()  # cleared/discarded again
            self.assertEqual(len(rootLayer.subLayerPaths), 0)
            cmds.undo()  # layers are back
            self.assertEqual(rootLayer.subLayerPaths[0], childLayerId)
            childLayer = Sdf.Layer.Find(childLayerId)
            self.assertEqual(childLayer.subLayerPaths[0], grandChildLayerId)

    def testRemoveEditTarget(self):

        shapePath, stage = getCleanMayaStage()
        rootLayer = stage.GetRootLayer()
        rootLayerID = rootLayer.identifier

        def setAndRemoveEditTargetRecursive(layer, index, editLayer):
            cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=editLayer)
            cmds.mayaUsdLayerEditor(layer.identifier, edit=True, removeSubPath=[index,shapePath])
            self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)
            cmds.undo()
            self.assertEqual(stage.GetEditTarget().GetLayer().identifier, editLayer)

            subLayer = Sdf.Layer.FindRelativeToLayer(layer, editLayer)
            for subLayerOffset, subLayerId in enumerate(subLayer.subLayerPaths):
                setAndRemoveEditTargetRecursive(subLayer, subLayerOffset, subLayerId) 

        # build a layer hierarchy
        layerColorId = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="Color")[0]
        myLayerId = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="MyLayer")[0]

        layerRedId = cmds.mayaUsdLayerEditor(layerColorId, edit=True, addAnonymous="Red")[0]
        layerGreen = cmds.mayaUsdLayerEditor(layerColorId, edit=True, addAnonymous="Green")[0]
        layerBlue = cmds.mayaUsdLayerEditor(layerColorId, edit=True, addAnonymous="Blue")[0]

        layerDarkRed = cmds.mayaUsdLayerEditor(layerRedId, edit=True, addAnonymous="DarkRed")[0]
        layerLightRed = cmds.mayaUsdLayerEditor(layerRedId, edit=True, addAnonymous="LightRed")[0]

        mySubLayerId = cmds.mayaUsdLayerEditor(myLayerId, edit=True, addAnonymous="MySubLayer")[0]

        # traverse the layer tree
        # for each layer, set it as the edit target and remove it.
        for subLayerOffset, subLayerPath in enumerate(rootLayer.subLayerPaths):
            setAndRemoveEditTargetRecursive(rootLayer, subLayerOffset, subLayerPath)

        #
        # Test when the editTarget's parent (direct/ indirect) layer is removed
        #
        cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=layerDarkRed)
 
        # remove the Red layer (direct parent of DarkRed layer)
        cmds.mayaUsdLayerEditor(layerColorId, edit=True, removeSubPath=[2,shapePath])
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)
        cmds.undo()
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, layerDarkRed)

        # remove the Color layer (indirect parent of DarkRed layer)
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[1,shapePath])
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)
        cmds.undo()
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, layerDarkRed)

        #
        # Test with a layer that is a sublayer multiple times.
        #
        sharedLayerFile = tempfile.NamedTemporaryFile(suffix=".usda", prefix="sharedLayer", delete=False, mode="w")
        sharedLayerFile.write("#usda 1.0")
        sharedLayerFile.close()

        sharedLayer = path.normcase(sharedLayerFile.name)

        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[0, sharedLayer])
        cmds.mayaUsdLayerEditor(mySubLayerId, edit=True, insertSubPath=[0, sharedLayer])

        cmds.mayaUsdEditTarget(shapePath, edit=True, editTarget=sharedLayer)

        # remove the sharedLayer under the root layer.
        # the edit target should still be the shared layer
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[0,shapePath])
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)
        cmds.undo()
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)

        # remove the sharedLayer under the MySubLayer.
        # the edit target should still be the shared layer
        cmds.mayaUsdLayerEditor(mySubLayerId, edit=True, removeSubPath=[0,shapePath])
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)
        cmds.undo()
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)

        # remove MySubLayer (Direct parent of SharedLayer).
        # the edit target should still be the shared layer
        cmds.mayaUsdLayerEditor(myLayerId, edit=True, removeSubPath=[0,shapePath])
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)
        cmds.undo()
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)

        # remove MyLayer (Indirect parent of SharedLayer).
        # the edit target should still be the shared layer
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[0,shapePath])
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)
        cmds.undo()
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)

        # remove SharedLayer everywhere.
        # the edit target should become the root layer
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, removeSubPath=[0,shapePath])
        cmds.mayaUsdLayerEditor(mySubLayerId, edit=True, removeSubPath=[0,shapePath])
        self.assertEqual(stage.GetEditTarget().GetLayer().identifier, rootLayerID)
        cmds.undo()
        self.assertEqual(path.normcase(stage.GetEditTarget().GetLayer().identifier), sharedLayer)

    def testRemoveMultipleLayers(self):

        shapePath, stage = getCleanMayaStage()
        rootLayer = stage.GetRootLayer()
        rootLayerID = rootLayer.identifier

        # Add six layers
        layerNames = []
        for index in range(1,7):
            layerName = "layer%d" % index
            layerName = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True,  addAnonymous=layerName)
            layerNames.append(layerName[0])

        # Verify that the root has the expected six layers.
        def subLayerNames():
            return list(rootLayer.subLayerPaths)

        def verifySubLayers(expectedNames):
            rootSubLayers = subLayerNames()
            self.assertEqual(len(expectedNames), len(rootSubLayers))
            for name in expectedNames:
                self.assertIn(name, rootSubLayers)

        verifySubLayers(layerNames)

        # Remove layers 0 and 1 and verify that the correct layers have been removed.
        #
        # Note: when inserted in USD, layers are added at the top, so layer 0 and 1 are
        #       layer5 and layer6.
        cmds.mayaUsdLayerEditor(rootLayer.identifier, removeSubPath=((0, shapePath), (1, shapePath)))
        verifySubLayers(layerNames[0:4])

    def testMoveSubPath(self):
        """ test 'mayaUsdLayerEditor' command 'moveSubPath' paramater """

        def moveSubPath(parentLayer, subPath, newParentLayer, index, originalSubLayerPaths, newSubLayerPaths):
            cmds.mayaUsdLayerEditor(parentLayer.identifier, edit=True, moveSubPath=[subPath, newParentLayer.identifier, index])
            self.assertEqual(newParentLayer.subLayerPaths, newSubLayerPaths)

            cmds.undo()
            self.assertEqual(newParentLayer.subLayerPaths, originalSubLayerPaths)

            cmds.redo()
            self.assertEqual(newParentLayer.subLayerPaths, newSubLayerPaths)

            cmds.undo()
            self.assertEqual(newParentLayer.subLayerPaths, originalSubLayerPaths)

        def moveElement(list, item, index):
            l = list[:] #copy the list
            l.remove(item)
            l.insert(index, item)
            return l

        shapePath, stage = getCleanMayaStage()
        rootLayer = stage.GetRootLayer()

        layerId1 = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="MyLayer1")[0]
        layerId2 = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="MyLayer2")[0]
        layerId3 = cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, addAnonymous="MyLayer3")[0]

        originalSubLayerPaths = [layerId3, layerId2, layerId1]
        self.assertEqual(rootLayer.subLayerPaths, originalSubLayerPaths)

        # Test moving each layers under the root layer to any valid index in the root layer
        # and test out-of-bound index
        for layer in originalSubLayerPaths:
            for i in range(len(originalSubLayerPaths)):
                expectedSubPath = moveElement(originalSubLayerPaths, layer, i)
                moveSubPath(rootLayer, layer, rootLayer, i, originalSubLayerPaths, expectedSubPath)

                with self.assertRaises(RuntimeError):
                    cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, moveSubPath=[layer, rootLayer.identifier, len(originalSubLayerPaths)])
                self.assertEqual(rootLayer.subLayerPaths, originalSubLayerPaths)

        #
        # Test moving sublayer (layer2 and layer3) inside a new parent layer (layer1)
        #

        layer1 = Sdf.Layer.Find(layerId1)     
        self.assertTrue(len(layer1.subLayerPaths) == 0)

        # layer1 has no subLayer so index 1 is invalide
        with self.assertRaises(RuntimeError):
            cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, moveSubPath=[layerId3, layerId1, 1])
        self.assertTrue(len(layer1.subLayerPaths) == 0)
        self.assertEqual(rootLayer.subLayerPaths, originalSubLayerPaths)

        # Move layer3 from the root layer inside layer1 at index 0
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, moveSubPath=[layerId3, layerId1, 0])
        self.assertEqual(rootLayer.subLayerPaths, [layerId2, layerId1])
        self.assertEqual(layer1.subLayerPaths, [layerId3])

        # Move layer2 from the root layer inside layer1 at index 0
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, moveSubPath=[layerId2, layerId1, 0])
        self.assertEqual(rootLayer.subLayerPaths, [layerId1])
        self.assertEqual(layer1.subLayerPaths, [layerId2, layerId3])

        cmds.undo()
        self.assertEqual(rootLayer.subLayerPaths, [layerId2, layerId1])
        self.assertEqual(layer1.subLayerPaths, [layerId3])

        # Move layer2 from the root layer inside layer1 at index 1
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, moveSubPath=[layerId2, layerId1, 1])
        self.assertEqual(rootLayer.subLayerPaths, [layerId1])
        self.assertEqual(layer1.subLayerPaths, [layerId3, layerId2])

        cmds.undo()
        self.assertEqual(rootLayer.subLayerPaths, [layerId2, layerId1])
        self.assertEqual(layer1.subLayerPaths, [layerId3])

        # Undo moving layer3 inside layer1
        cmds.undo()
        self.assertEqual(rootLayer.subLayerPaths, [layerId3, layerId2, layerId1])
        self.assertEqual(layer1.subLayerPaths, [])

        # Insert layer3 inside layer1 and try to move the layer3 from the root layer
        # inside layer1. This should fail.
        cmds.mayaUsdLayerEditor(layerId1, edit=True, insertSubPath=[0, layerId3])
        self.assertEqual(layer1.subLayerPaths, [layerId3])

        with self.assertRaises(RuntimeError):
            cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, moveSubPath=[layerId3, layerId1, 0])
        self.assertEqual(rootLayer.subLayerPaths, originalSubLayerPaths)
        self.assertEqual(layer1.subLayerPaths, [layerId3])

    def testMoveRelativeSubPath(self):
        """ test 'mayaUsdLayerEditor' command 'moveSubPath' paramater for relative subpaths """

        shapePath, stage = getCleanMayaStage()
        rootLayer = stage.GetRootLayer()

        # Create a temporary directory in the current diretory
        myDir = testUtils.TemporaryDirectory(suffix="testMoveRelativeSubPathDir")
        myDir_path, myDir_name = path.split(myDir.name)

        # Create a new usda file in the current diretory and add it to the root layer through absolute path
        absLayer1File = tempfile.NamedTemporaryFile(suffix=".usda", prefix="absLayer1", dir=myDir_path, delete=False, mode="w")
        absLayer1File.write("#usda 1.0")
        absLayer1File.close()
        absLayer1Id = path.normpath(absLayer1File.name)
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[0, absLayer1Id])
        absLayer1 = Sdf.Layer.Find(absLayer1Id)

        # Create a new usda file in the new temporary diretory and add it to the root layer through absolute path
        absLayer2File = tempfile.NamedTemporaryFile(suffix=".usda", prefix="absLayer2", dir=myDir.name, delete=False, mode="w")
        absLayer2File.write("#usda 1.0")
        absLayer2File.close()
        absLayer2Id = path.normpath(absLayer2File.name)
        cmds.mayaUsdLayerEditor(rootLayer.identifier, edit=True, insertSubPath=[1, absLayer2Id])
        absLayer2 = Sdf.Layer.Find(absLayer2Id)

        # Create a new usda file in the new temporary diretory and add it to absLayer2 through its relative path
        relLayerFile = tempfile.NamedTemporaryFile(suffix=".usda", prefix="relLayer", dir=myDir.name, delete=False, mode="w")
        relLayerFile.write("#usda 1.0")
        relLayerFile.close()
        relLayerFileId = path.basename(relLayerFile.name) # relative path
        cmds.mayaUsdLayerEditor(absLayer2.identifier, edit=True, insertSubPath=[0, relLayerFileId])

        # Now move the relative sublayer from absLayer2 to absLayer1
        cmds.mayaUsdLayerEditor(absLayer2.identifier, edit=True, moveSubPath=[relLayerFileId, absLayer1.identifier, 0])

        # The relative sublayer's path should change now to include the directory name
        relLayerNewFileId = myDir_name + "/" + relLayerFileId
        self.assertTrue(len(absLayer1.subLayerPaths) == 1)
        self.assertEqual(absLayer1.subLayerPaths[0], relLayerNewFileId)

    def testLockLayer(self):
        """ test 'mayaUsdLayerEditor' command 'lockLayer' paramater """
        
        # Helpers
        def createLayer(index):
            layer = Sdf.Layer.CreateAnonymous()
            stage.GetRootLayer().subLayerPaths.append(layer.identifier)
            return layer
        
        shapePath, stage = getCleanMayaStage()
        self.assertTrue(stage)
        
        subLayer = createLayer(0)
        self.assertTrue(subLayer.permissionToEdit)
        
        # Locking a layer
        cmds.mayaUsdLayerEditor(subLayer.identifier, edit=True, lockLayer=(1, 0, shapePath))
        self.assertFalse(subLayer.permissionToEdit)
        cmds.undo()
        self.assertTrue(subLayer.permissionToEdit)
        cmds.redo()
        self.assertFalse(subLayer.permissionToEdit)
        # Unlocking a layer
        cmds.mayaUsdLayerEditor(subLayer.identifier, edit=True, lockLayer=(0, 0, shapePath))
        self.assertTrue(subLayer.permissionToEdit)
        # System locking a layer
        cmds.mayaUsdLayerEditor(subLayer.identifier, edit=True, lockLayer=(2, 0, shapePath))
        self.assertFalse(subLayer.permissionToEdit)
        self.assertFalse(subLayer.permissionToSave)
        cmds.undo()
        self.assertTrue(subLayer.permissionToEdit)

    def testLockLayerAndSubLayers(self):
        # FileBacked Layer Write Permission
        # 1- Loading the test scene
        rootLayerPath = testUtils.getTestScene("layerLocking", "layerLocking.usda")
        stage = Usd.Stage.Open(rootLayerPath)
        topLayer = stage.GetRootLayer();
        layerLockingShapes = cmds.ls(type="mayaUsdProxyShapeBase", long=True)
        proxyShapePath = layerLockingShapes[0]
        # 2- Setting a system lock on a layer loaded from a file
        cmds.mayaUsdLayerEditor(topLayer.identifier, edit=True, lockLayer=(2, 0, proxyShapePath))
        self.assertFalse(topLayer.permissionToEdit)
        self.assertFalse(topLayer.permissionToSave)
        # 3- Refreshing the system lock should remove the lock if the file is writable
        cmds.mayaUsdLayerEditor(topLayer.identifier, edit=True, refreshSystemLock=(proxyShapePath, 1))
        self.assertTrue(topLayer.permissionToEdit)
        self.assertTrue(topLayer.permissionToSave)
        
    def testLayerLockWritePermission(self):
        # Locking a layer and its sublayer
        # 1- Loading the test scene
        rootLayerPath = testUtils.getTestScene("layerLocking", "layerLocking.usda")
        stage = Usd.Stage.Open(rootLayerPath)
        topLayer = stage.GetRootLayer();
        layerLockingShapes = cmds.ls(type="mayaUsdProxyShapeBase", long=True)
        proxyShapePath = layerLockingShapes[0]
        # 2- Setting a lock on the top layer with the option to lock its sublayer
        cmds.mayaUsdLayerEditor(topLayer.identifier, edit=True, lockLayer=(1, 1, proxyShapePath))
        self.assertFalse(topLayer.permissionToEdit)
        # 3- Check that sublayer is also locked
        subLayer = Sdf.Layer.FindRelativeToLayer(topLayer, topLayer.subLayerPaths[0])
        self.assertFalse(subLayer.permissionToEdit)
        # 4- Checking that no layers are modifiable
        self.assertFalse(mayaUsdUfe.isAnyLayerModifiable(stage))
        # 5- Undo and check that both top and sublayer are unlocked
        cmds.undo()
        self.assertTrue(topLayer.permissionToEdit)
        self.assertTrue(subLayer.permissionToEdit)
        # 6- Checking that at least one layer is modifiable
        self.assertTrue(mayaUsdUfe.isAnyLayerModifiable(stage))
    
    def testMuteLayer(self):
        """ test 'mayaUsdLayerEditor' command 'muteLayer' paramater """

        def testMuteLayerImpl(addLayerFunc):

            def checkMuted(layer, stage):
                # Make sure the layer is muted inside the stage.
                self.assertTrue(stage.IsLayerMuted(layer.identifier))
                self.assertTrue(layer.identifier in stage.GetMutedLayers())
                # Make sure the stage does not used the muted layer
                self.assertFalse(layer in stage.GetLayerStack(False))
                self.assertFalse(layer in stage.GetUsedLayers(False))

            def checkUnMuted(layer, stage):
                self.assertFalse(stage.IsLayerMuted(layer.identifier))
                self.assertFalse(layer.identifier in stage.GetMutedLayers())
                self.assertTrue(layer in stage.GetLayerStack(False))
                self.assertTrue(layer in stage.GetUsedLayers(False))

            shapePath, stage = getCleanMayaStage()
            rootLayer = stage.GetRootLayer()

            layer = addLayerFunc(rootLayer)
            
            checkUnMuted(layer, stage)
            
            # Mute the layer
            cmds.mayaUsdLayerEditor(layer.identifier, edit=True, muteLayer=(True, shapePath))
            checkMuted(layer, stage)
        
            # undo mute
            cmds.undo()
            checkUnMuted(layer, stage)

            # redo mute
            cmds.redo()
            checkMuted(layer, stage)
        
        # Add an anonymous layer under the "parentLayer" 
        def addAnonymousLayer(parentLayer):
            layerId = cmds.mayaUsdLayerEditor(parentLayer.identifier, edit=True, addAnonymous="MyLayer")[0]
            layer = Sdf.Layer.Find(layerId)
            return layer

        # Add a layer baked file under the "parentLayer"
        # The layer is added by using it's identifier.
        def addFileBakedLayer(parentLayer):
            tempFile = tempfile.NamedTemporaryFile(suffix=".usda", delete=False, mode="w")
            tempFile.write("#usda 1.0")
            tempFile.close()
            layer = Sdf.Layer.FindOrOpen(tempFile.name)
            cmds.mayaUsdLayerEditor(parentLayer.identifier, edit=True, insertSubPath=[0, layer.identifier])
            self.assertTrue(layer)
            return layer

        # Add a layer baked file under the "parentLayer"
        # The layer is added by using it's filesystem path.
        def addFileBakedLayerByPath(parentLayer):
            tempFile = tempfile.NamedTemporaryFile(suffix=".usda", delete=False, mode="w")
            tempFile.write("#usda 1.0")
            tempFile.close()
            cmds.mayaUsdLayerEditor(parentLayer.identifier, edit=True, insertSubPath=[0, tempFile.name])
            layer = Sdf.Layer.FindOrOpen(tempFile.name)
            self.assertTrue(layer)
            return layer

        testMuteLayerImpl(addAnonymousLayer)
        testMuteLayerImpl(addFileBakedLayer)
        testMuteLayerImpl(addFileBakedLayerByPath)

    def testPathRelativeToMayaSceneFile(self):
        # Function mayaUsdLib.Util.getPathRelativeToMayaSceneFile is used during 
        # USD root layer saving/loading, and since we cannot test the full workflow
        # as it opens a file dialog and requires user input, we make sure to test 
        # the function itself here.
        
        ballFilePath = path.normpath(testUtils.getTestScene('ballset', 'StandaloneScene', 'top_layer.usda')).replace('\\', '/')

        # Without Maya scene file, the absolute path is returned
        cmds.file(newFile=True, force=True)
        filePathAbs = mayaUsd.lib.Util.getPathRelativeToMayaSceneFile(ballFilePath)
        self.assertEqual(filePathAbs, ballFilePath)

        # With Maya scene file, the relative path is returned
        mayaSceneFilePath = testUtils.getTestScene("ballset", "StandaloneScene", "top_layer.ma")
        cmds.file(mayaSceneFilePath, force=True, open=True)
        filePathRel = mayaUsd.lib.Util.getPathRelativeToMayaSceneFile(ballFilePath)
        self.assertEqual(filePathRel, 'top_layer.usda')
