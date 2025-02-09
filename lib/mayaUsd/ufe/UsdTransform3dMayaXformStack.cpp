//
// Copyright 2020 Autodesk
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
#include "UsdTransform3dMayaXformStack.h"

#include "UsdSetXformOpUndoableCommandBase.h"
#include "private/UfeNotifGuard.h"

#include <mayaUsd/fileio/utils/xformStack.h>
#include <mayaUsd/ufe/RotationUtils.h>
#include <mayaUsd/ufe/UsdTransform3dUndoableCommands.h>
#include <mayaUsd/ufe/Utils.h>

#include <usdUfe/ufe/Utils.h>
#include <usdUfe/undo/UsdUndoBlock.h>
#include <usdUfe/undo/UsdUndoableItem.h>

#include <maya/MEulerRotation.h>
#include <maya/MGlobal.h>

#include <cstring>
#include <functional>
#include <map>
#include <typeinfo>

PXR_NAMESPACE_USING_DIRECTIVE

namespace {

using BaseUndoableCommand = Ufe::BaseUndoableCommand;
using OpFunc = std::function<UsdGeomXformOp(const BaseUndoableCommand&, UsdUndoableItem&)>;

using namespace MayaUsd::ufe;

// Type traits for GfVec precision.
template <class V> struct OpPrecision
{
    static UsdGeomXformOp::Precision precision;
};

template <>
UsdGeomXformOp::Precision OpPrecision<GfVec3f>::precision = UsdGeomXformOp::PrecisionFloat;

template <>
UsdGeomXformOp::Precision OpPrecision<GfVec3d>::precision = UsdGeomXformOp::PrecisionDouble;

VtValue getValue(const PXR_NS::UsdAttribute& attr, const UsdTimeCode& time)
{
    VtValue value;
    attr.Get(&value, time);
    return value;
}

// This utility function is used to avoid the TF_VERIFY message thrown up
// when GetAttribute() is called with an empty token.
PXR_NS::UsdAttribute getUsdPrimAttribute(const UsdPrim& prim, const TfToken& attrName)
{
    return !attrName.IsEmpty() ? prim.GetAttribute(attrName) : PXR_NS::UsdAttribute();
}

// UsdMayaXformStack::FindOpIndex() requires an inconvenient isInvertedTwin
// argument, various rotate transform op equivalences in a separate
// UsdMayaXformStack::IsCompatibleType().  Just roll our own op name to
// Maya transform stack index position.
const std::unordered_map<TfToken, UsdTransform3dMayaXformStack::OpNdx, TfToken::HashFunctor>
    gOpNameToNdx {
        { TfToken("xformOp:translate"), UsdTransform3dMayaXformStack::NdxTranslate },
        // Note: this matches the USD common xformOp name.
        { TfToken("xformOp:translate:pivot"), UsdTransform3dMayaXformStack::NdxPivot },
        { TfToken("xformOp:translate:rotatePivotTranslate"),
          UsdTransform3dMayaXformStack::NdxRotatePivotTranslate },
        { TfToken("xformOp:translate:rotatePivot"), UsdTransform3dMayaXformStack::NdxRotatePivot },
        { TfToken("xformOp:rotateX"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateY"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateZ"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateXYZ"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateXZY"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateYXZ"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateYZX"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateZXY"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateZYX"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:orient"), UsdTransform3dMayaXformStack::NdxRotate },
        { TfToken("xformOp:rotateXYZ:rotateAxis"), UsdTransform3dMayaXformStack::NdxRotateAxis },
        { TfToken("!invert!xformOp:translate:rotatePivot"),
          UsdTransform3dMayaXformStack::NdxRotatePivotInverse },
        { TfToken("xformOp:translate:scalePivotTranslate"),
          UsdTransform3dMayaXformStack::NdxScalePivotTranslate },
        { TfToken("xformOp:translate:scalePivot"), UsdTransform3dMayaXformStack::NdxScalePivot },
        { TfToken("xformOp:transform:shear"), UsdTransform3dMayaXformStack::NdxShear },
        { TfToken("xformOp:scale"), UsdTransform3dMayaXformStack::NdxScale },
        { TfToken("!invert!xformOp:translate:scalePivot"),
          UsdTransform3dMayaXformStack::NdxScalePivotInverse },
        // Note: this matches the USD common xformOp name.
        { TfToken("!invert!xformOp:translate:pivot"),
          UsdTransform3dMayaXformStack::NdxPivotInverse }
    };

} // namespace

namespace MAYAUSD_NS_DEF {
namespace ufe {

namespace {

bool setXformOpOrder(const UsdGeomXformable& xformable)
{
    // Simply adding a transform op appends to the op order vector.  Therefore,
    // after addition, we must sort the ops to preserve Maya transform stack
    // ordering.  Use the Maya transform stack indices to add to a map, then
    // simply traverse the map to obtain the transform ops in order.
    std::map<UsdTransform3dMayaXformStack::OpNdx, UsdGeomXformOp> orderedOps;
    bool                                                          resetsXformStack = false;
    auto oldOrder = xformable.GetOrderedXformOps(&resetsXformStack);
    for (const auto& op : oldOrder) {
        auto ndx = gOpNameToNdx.at(op.GetOpName());
        orderedOps[ndx] = op;
    }

    // Set the transform op order attribute.
    std::vector<UsdGeomXformOp> newOrder;
    newOrder.reserve(oldOrder.size());
    for (const auto& orderedOp : orderedOps) {
        const auto& op = orderedOp.second;
        newOrder.emplace_back(op);
    }

    return xformable.SetXformOpOrder(newOrder, resetsXformStack);
}

using NextTransform3dFn = std::function<Ufe::Transform3d::Ptr()>;

bool hasValidSuffix(const std::vector<UsdGeomXformOp>& xformOps)
{
    TF_FOR_ALL(iter, xformOps)
    {
        const UsdGeomXformOp& xformOp = *iter;
        auto                  ndx = gOpNameToNdx.find(xformOp.GetName());
        if (ndx == gOpNameToNdx.end())
            return false;
    }
    return true;
}

Ufe::Transform3d::Ptr
createTransform3d(const Ufe::SceneItem::Ptr& item, NextTransform3dFn nextTransform3dFn)
{
    UsdSceneItem::Ptr usdItem = std::dynamic_pointer_cast<UsdSceneItem>(item);

    if (!usdItem) {
        return nullptr;
    }

    // If the prim isn't transformable, can't create a Transform3d interface
    // for it.
    UsdGeomXformable xformSchema(usdItem->prim());
    if (!xformSchema) {
        return nullptr;
    }
    bool resetsXformStack = false;
    auto xformOps = xformSchema.GetOrderedXformOps(&resetsXformStack);

    // Early out: if there are no transform ops yet, it's a match.
    if (xformOps.empty()) {
        return UsdTransform3dMayaXformStack::create(usdItem);
    }

    // reject tokens not in gOpNameToNdx
    if (!hasValidSuffix(xformOps))
        return nextTransform3dFn();

    // If the prim supports the Maya transform stack, create a Maya transform
    // stack interface for it, otherwise delegate to the next handler in the
    // chain of responsibility.
    auto stackOps = UsdMayaXformStack::MayaStack().MatchingSubstack(xformOps);

    return stackOps.empty() ? nextTransform3dFn() : UsdTransform3dMayaXformStack::create(usdItem);
}

// Helper class to factor out common code for translate, rotate, scale
// undoable commands.
//
// We must do a careful dance due to historic reasons and the way Maya handle
// interactive commands:
//
//     - These commands can be wrapped inside other commands which may
//       use their own UsdUndoBlock. In particular, we must not try to
//       undo an attribute creation if it was not yet created.
//
//     - Maya can call undo and set-value before first executing the
//       command. In particular, when using manipualtion tools, Maya
//       will usually do loops of undo/set-value/execute, thus beginning
//       by undoing a command that was never executed.
//
//     - As a general rule, when undoing, we want to remove any attributes
//       that were created when first executed.
//
//     - When redoing some commands after an undo, Maya will update the
//       value to be set with an incorrect value when operating in object
//       space, which must be ignored.
//
// Those things are what the prepare-op/recreate-op/remove-op functions are
// aimed to support. Also, we must only capture the initial value the first
// time thevalue is modified, to support both the inital undo/set-value and
// avoid losing the initial value on repeat set-value.
class UsdTRSUndoableCmdBase : public UsdSetXformOpUndoableCommandBase
{
public:
    UsdTRSUndoableCmdBase(
        const VtValue&     newOpValue,
        const Ufe::Path&   path,
        OpFunc             opFunc,
        const UsdTimeCode& writeTime)
        : UsdSetXformOpUndoableCommandBase(newOpValue, path, writeTime)
        , _op()
        , _opFunc(std::move(opFunc))
    {
    }

protected:
    void createOpIfNeeded(UsdUndoableItem& undoableItem) override
    {
        if (_op)
            return;

        _op = _opFunc(*this, undoableItem);
    }

    void setValue(const VtValue& v, const UsdTimeCode& writeTime) override
    {
        if (!_op)
            return;

        if (v.IsEmpty())
            return;

        auto attr = _op.GetAttr();
        if (!attr)
            return;

        attr.Set(v, writeTime);
    }

    VtValue getValue(const UsdTimeCode& readTime) const override
    {
        if (!_op)
            return {};

        auto attr = _op.GetAttr();
        if (!attr)
            return {};

        VtValue value;
        attr.Get(&value, readTime);
        return value;
    }

private:
    UsdGeomXformOp _op;
    OpFunc         _opFunc;
};

// UsdRotatePivotTranslateUndoableCmd uses hard-coded USD common transform API
// single pivot attribute name, not reusable.
template <class V> class UsdVecOpUndoableCmd : public UsdTRSUndoableCmdBase
{
public:
    UsdVecOpUndoableCmd(
        const V&           v,
        const Ufe::Path&   path,
        OpFunc             opFunc,
        const UsdTimeCode& writeTime)
        : UsdTRSUndoableCmdBase(VtValue(v), path, opFunc, writeTime)
    {
    }

    // Executes the command by setting the translation onto the transform op.
    bool set(double x, double y, double z) override
    {
        VtValue v;
        v = V(x, y, z);
        updateNewValue(v);
        return true;
    }
};

class UsdRotateOpUndoableCmd : public UsdTRSUndoableCmdBase
{
public:
    UsdRotateOpUndoableCmd(
        const GfVec3f&                                  r,
        const Ufe::Path&                                path,
        OpFunc                                          opFunc,
        UsdTransform3dMayaXformStack::CvtRotXYZToAttrFn cvt,
        const UsdTimeCode&                              writeTime)
        : UsdTRSUndoableCmdBase(VtValue(r), path, opFunc, writeTime)
        , _cvtRotXYZToAttr(cvt)
    {
    }

    // Executes the command by setting the rotation onto the transform op.
    bool set(double x, double y, double z) override
    {
        VtValue v;
        v = _cvtRotXYZToAttr(x, y, z);
        updateNewValue(v);
        return true;
    }

private:
    // Convert from UFE RotXYZ rotation to a value for the transform op.
    UsdTransform3dMayaXformStack::CvtRotXYZToAttrFn _cvtRotXYZToAttr;
};

struct SceneItemHolder
{
    SceneItemHolder(const BaseUndoableCommand& cmd)
    {
        _sceneItem = std::dynamic_pointer_cast<UsdSceneItem>(cmd.sceneItem());
        if (!_sceneItem) {
            throw std::runtime_error("Cannot transform invalid scene item");
        }
    }

    UsdSceneItem& item() const { return *_sceneItem; }

private:
    std::shared_ptr<UsdSceneItem> _sceneItem;
};

} // namespace

UsdTransform3dMayaXformStack::UsdTransform3dMayaXformStack(const UsdSceneItem::Ptr& item)
    : UsdTransform3dBase(item)
    , _xformable(prim())
{
    if (!TF_VERIFY(_xformable)) {
        throw std::runtime_error("Invalid scene item for transform stack");
    }
}

/* static */
UsdTransform3dMayaXformStack::Ptr
UsdTransform3dMayaXformStack::create(const UsdSceneItem::Ptr& item)
{
    return std::make_shared<UsdTransform3dMayaXformStack>(item);
}

Ufe::Vector3d UsdTransform3dMayaXformStack::translation() const
{
    return getVector3d<GfVec3d>(
        UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, getTRSOpSuffix()));
}

Ufe::Vector3d UsdTransform3dMayaXformStack::rotation() const
{
    if (!hasOp(NdxRotate)) {
        return Ufe::Vector3d(0, 0, 0);
    }
    UsdGeomXformOp r = getOp(NdxRotate);
    TF_DEV_AXIOM(r);
    if (!r.GetAttr().HasValue()) {
        return Ufe::Vector3d(0, 0, 0);
    }

    CvtRotXYZFromAttrFn cvt = getCvtRotXYZFromAttrFn(r.GetOpName());
    return cvt(getValue(r.GetAttr(), getTime(path())));
}

Ufe::Vector3d UsdTransform3dMayaXformStack::scale() const
{
    if (!hasOp(NdxScale)) {
        return Ufe::Vector3d(1, 1, 1);
    }
    UsdGeomXformOp s = getOp(NdxScale);
    TF_DEV_AXIOM(s);
    if (!s.GetAttr().HasValue()) {
        return Ufe::Vector3d(1, 1, 1);
    }

    GfVec3f v;
    s.Get(&v, getTime(path()));
    return toUfe(v);
}

Ufe::TranslateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::translateCmd(double x, double y, double z)
{
    return setVector3dCmd(
        GfVec3d(x, y, z),
        UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, getTRSOpSuffix()),
        getTRSOpSuffix());
}

Ufe::RotateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::rotateCmd(double x, double y, double z)
{
    UsdGeomXformOp op;
    TfToken        attrName;
    const bool     hasRotate = hasOp(NdxRotate);
    if (hasRotate) {
        op = getOp(NdxRotate);
        attrName = op.GetOpName();
    }

    // Return null command if the attribute edit is not allowed.
    std::string errMsg;
    if (!isAttributeEditAllowed(attrName, errMsg)) {
        MGlobal::displayError(errMsg.c_str());
        return nullptr;
    }

    // If there is no rotate transform op, we will create a RotXYZ.
    GfVec3f           v(x, y, z);
    CvtRotXYZToAttrFn cvt = hasRotate ? getCvtRotXYZToAttrFn(op.GetOpName()) : toXYZ;

    auto f
        = OpFunc([attrName, opSuffix = getTRSOpSuffix(), setXformOpOrderFn = getXformOpOrderFn()](
                     const BaseUndoableCommand& cmd, UsdUndoableItem& undoableItem) {
              SceneItemHolder usdSceneItem(cmd);

              auto attr = getUsdPrimAttribute(usdSceneItem.item().prim(), attrName);
              if (attr) {
                  return UsdGeomXformOp(attr);
              } else {
                  UsdUndoBlock undoBlock(&undoableItem);

                  // Use notification guard, otherwise will generate one notification
                  // for the xform op add, and another for the reorder.
                  UsdUfe::InTransform3dChange guard(cmd.path());
                  UsdGeomXformable            xformable(usdSceneItem.item().prim());

                  auto r = xformable.AddRotateXYZOp(UsdGeomXformOp::PrecisionFloat, opSuffix);
                  if (!r) {
                      throw std::runtime_error("Cannot add rotation transform operation");
                  }
                  if (!setXformOpOrderFn(xformable)) {
                      throw std::runtime_error("Cannot set rotation transform operation");
                  }

                  return r;
              }
          });

    return std::make_shared<UsdRotateOpUndoableCmd>(
        v, path(), std::move(f), cvt, UsdTimeCode::Default());
}

Ufe::ScaleUndoableCommand::Ptr UsdTransform3dMayaXformStack::scaleCmd(double x, double y, double z)
{
    UsdGeomXformOp op;
    TfToken        attrName;
    if (hasOp(NdxScale)) {
        op = getOp(NdxScale);
        attrName = op.GetOpName();
    }

    // Return null command if the attribute edit is not allowed.
    std::string errMsg;
    if (!isAttributeEditAllowed(attrName, errMsg)) {
        MGlobal::displayError(errMsg.c_str());
        return nullptr;
    }

    GfVec3f v(x, y, z);
    auto    f
        = OpFunc([attrName, opSuffix = getTRSOpSuffix(), setXformOpOrderFn = getXformOpOrderFn()](
                     const BaseUndoableCommand& cmd, UsdUndoableItem& undoableItem) {
              SceneItemHolder usdSceneItem(cmd);

              auto attr = getUsdPrimAttribute(usdSceneItem.item().prim(), attrName);
              if (attr) {
                  return UsdGeomXformOp(attr);
              } else {
                  UsdUndoBlock undoBlock(&undoableItem);

                  UsdUfe::InTransform3dChange guard(cmd.path());
                  UsdGeomXformable            xformable(usdSceneItem.item().prim());

                  auto s = xformable.AddScaleOp(UsdGeomXformOp::PrecisionFloat, opSuffix);
                  if (!s) {
                      throw std::runtime_error("Cannot add scaling transform operation");
                  }
                  if (!setXformOpOrderFn(xformable)) {
                      throw std::runtime_error("Cannot set scaling transform operation");
                  }

                  return s;
              }
          });

    return std::make_shared<UsdVecOpUndoableCmd<GfVec3f>>(
        v, path(), std::move(f), UsdTimeCode::Default());
}

Ufe::TranslateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::rotatePivotCmd(double x, double y, double z)
{
    return pivotCmd(getOpSuffix(NdxRotatePivot), x, y, z);
}

Ufe::Vector3d UsdTransform3dMayaXformStack::rotatePivot() const
{
    return getVector3d<GfVec3f>(
        UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, getOpSuffix(NdxRotatePivot)));
}

Ufe::TranslateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::scalePivotCmd(double x, double y, double z)
{
    return pivotCmd(getOpSuffix(NdxScalePivot), x, y, z);
}

Ufe::Vector3d UsdTransform3dMayaXformStack::scalePivot() const
{
    return getVector3d<GfVec3f>(
        UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, getOpSuffix(NdxScalePivot)));
}

Ufe::TranslateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::translateRotatePivotCmd(double x, double y, double z)
{
    auto opSuffix = getOpSuffix(NdxRotatePivotTranslate);
    auto attrName = UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, opSuffix);
    return setVector3dCmd(GfVec3f(x, y, z), attrName, opSuffix);
}

Ufe::Vector3d UsdTransform3dMayaXformStack::rotatePivotTranslation() const
{
    return getVector3d<GfVec3f>(UsdGeomXformOp::GetOpName(
        UsdGeomXformOp::TypeTranslate, getOpSuffix(NdxRotatePivotTranslate)));
}

Ufe::TranslateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::translateScalePivotCmd(double x, double y, double z)
{
    auto opSuffix = getOpSuffix(NdxScalePivotTranslate);
    auto attrName = UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, opSuffix);
    return setVector3dCmd(GfVec3f(x, y, z), attrName, opSuffix);
}

Ufe::Vector3d UsdTransform3dMayaXformStack::scalePivotTranslation() const
{
    return getVector3d<GfVec3f>(UsdGeomXformOp::GetOpName(
        UsdGeomXformOp::TypeTranslate, getOpSuffix(NdxScalePivotTranslate)));
}

template <class V>
Ufe::Vector3d UsdTransform3dMayaXformStack::getVector3d(const TfToken& attrName) const
{
    // If the attribute doesn't exist or have a value yet, return a zero vector.
    auto attr = prim().GetAttribute(attrName);
    if (!attr || !attr.HasValue()) {
        return Ufe::Vector3d(0, 0, 0);
    }

    V v;
    UsdGeomXformOp(attr).Get(&v, getTime(path()));
    return toUfe(v);
}

template <class V>
Ufe::SetVector3dUndoableCommand::Ptr UsdTransform3dMayaXformStack::setVector3dCmd(
    const V&       v,
    const TfToken& attrName,
    const TfToken& opSuffix)
{
    // Return null command if the attribute edit is not allowed.
    std::string errMsg;
    if (!isAttributeEditAllowed(attrName, errMsg)) {
        MGlobal::displayError(errMsg.c_str());
        return nullptr;
    }

    auto setXformOpOrderFn = getXformOpOrderFn();
    auto f = OpFunc(
        // MAYA-108612: generalized lambda capture below is incorrect with
        // gcc 6.3.1 on Linux.  Call to getXformOpOrderFn() is non-virtual;
        // work around by calling in function body.  PPT, 11-Jan-2021.
        // [opSuffix, setXformOpOrderFn = getXformOpOrderFn(), v](const BaseUndoableCommand&
        // cmd) {
        [attrName, opSuffix, setXformOpOrderFn](
            const BaseUndoableCommand& cmd, UsdUndoableItem& undoableItem) {
            SceneItemHolder usdSceneItem(cmd);

            auto attr = getUsdPrimAttribute(usdSceneItem.item().prim(), attrName);
            if (attr) {
                return UsdGeomXformOp(attr);
            } else {
                UsdUndoBlock undoBlock(&undoableItem);

                UsdUfe::InTransform3dChange guard(cmd.path());
                UsdGeomXformable            xformable(usdSceneItem.item().prim());
                auto op = xformable.AddTranslateOp(OpPrecision<V>::precision, opSuffix);
                if (!op) {
                    throw std::runtime_error("Cannot add translation transform operation");
                }
                if (!setXformOpOrderFn(xformable)) {
                    throw std::runtime_error("Cannot set translation transform operation");
                }
                return op;
            }
        });

    return std::make_shared<UsdVecOpUndoableCmd<V>>(
        v, path(), std::move(f), UsdTimeCode::Default());
}

Ufe::TranslateUndoableCommand::Ptr
UsdTransform3dMayaXformStack::pivotCmd(const TfToken& pvtOpSuffix, double x, double y, double z)
{
    auto pvtAttrName = UsdGeomXformOp::GetOpName(UsdGeomXformOp::TypeTranslate, pvtOpSuffix);

    // Return null command if the attribute edit is not allowed.
    std::string errMsg;
    if (!isAttributeEditAllowed(pvtAttrName, errMsg)) {
        MGlobal::displayError(errMsg.c_str());
        return nullptr;
    }

    GfVec3f v(x, y, z);
    auto    f = OpFunc([pvtAttrName, pvtOpSuffix, setXformOpOrderFn = getXformOpOrderFn()](
                        const BaseUndoableCommand& cmd, UsdUndoableItem& undoableItem) {
        SceneItemHolder usdSceneItem(cmd);

        auto attr = usdSceneItem.item().prim().GetAttribute(pvtAttrName);
        if (attr) {
            auto attr = usdSceneItem.item().prim().GetAttribute(pvtAttrName);
            return UsdGeomXformOp(attr);
        } else {
            // Without a notification guard each operation (each transform op
            // addition, setting the attribute value, and setting the transform
            // op order) will notify.  Observers would see an object in an
            // inconsistent state, especially after pivot is added but before
            // its inverse is added --- this does not match the Maya transform
            // stack.  Use of SdfChangeBlock is discouraged when calling USD
            // APIs above Sdf, so use our own guard.

            UsdUndoBlock                undoBlock(&undoableItem);
            UsdUfe::InTransform3dChange guard(cmd.path());
            UsdGeomXformable            xformable(usdSceneItem.item().prim());
            auto p = xformable.AddTranslateOp(UsdGeomXformOp::PrecisionFloat, pvtOpSuffix);

            auto pInv = xformable.AddTranslateOp(
                UsdGeomXformOp::PrecisionFloat, pvtOpSuffix, /* isInverseOp */ true);
            if (!(p && pInv)) {
                throw std::runtime_error("Cannot add translation transform operation");
            }
            if (!setXformOpOrderFn(xformable)) {
                throw std::runtime_error("Cannot set translation transform operation");
            }
            return p;
        }
    });

    return std::make_shared<UsdVecOpUndoableCmd<GfVec3f>>(
        v, path(), std::move(f), UsdTimeCode::Default());
}

Ufe::SetMatrix4dUndoableCommand::Ptr
UsdTransform3dMayaXformStack::setMatrixCmd(const Ufe::Matrix4d& m)
{
    return std::make_shared<UsdSetMatrix4dUndoableCommand>(path(), m);
}

UsdTransform3dMayaXformStack::SetXformOpOrderFn
UsdTransform3dMayaXformStack::getXformOpOrderFn() const
{
    return setXformOpOrder;
}

std::map<UsdTransform3dMayaXformStack::OpNdx, UsdGeomXformOp>
UsdTransform3dMayaXformStack::getOrderedOps() const
{
    std::map<OpNdx, UsdGeomXformOp> orderedOps;
    bool                            resetsXformStack = false;
    auto                            ops = _xformable.GetOrderedXformOps(&resetsXformStack);
    for (const auto& op : ops) {
        auto ndx = gOpNameToNdx.at(op.GetOpName());
        orderedOps[ndx] = op;
    }
    return orderedOps;
}

bool UsdTransform3dMayaXformStack::hasOp(OpNdx ndx) const
{
    auto orderedOps = getOrderedOps();
    return orderedOps.find(ndx) != orderedOps.end();
}

UsdGeomXformOp UsdTransform3dMayaXformStack::getOp(OpNdx ndx) const
{
    auto orderedOps = getOrderedOps();
    return orderedOps.at(ndx);
}

TfToken UsdTransform3dMayaXformStack::getOpSuffix(OpNdx ndx) const
{
    static std::unordered_map<OpNdx, TfToken> opSuffix
        = { { NdxRotatePivotTranslate, UsdMayaXformStackTokens->rotatePivotTranslate },
            { NdxRotatePivot, UsdMayaXformStackTokens->rotatePivot },
            { NdxRotateAxis, UsdMayaXformStackTokens->rotateAxis },
            { NdxScalePivotTranslate, UsdMayaXformStackTokens->scalePivotTranslate },
            { NdxScalePivot, UsdMayaXformStackTokens->scalePivot },
            { NdxShear, UsdMayaXformStackTokens->shear } };
    return opSuffix.at(ndx);
}

TfToken UsdTransform3dMayaXformStack::getTRSOpSuffix() const { return TfToken(); }

UsdTransform3dMayaXformStack::CvtRotXYZFromAttrFn
UsdTransform3dMayaXformStack::getCvtRotXYZFromAttrFn(const TfToken& opName) const
{
    static std::unordered_map<TfToken, CvtRotXYZFromAttrFn, TfToken::HashFunctor> cvt = {
        { TfToken("xformOp:rotateX"), fromX },     { TfToken("xformOp:rotateY"), fromY },
        { TfToken("xformOp:rotateZ"), fromZ },     { TfToken("xformOp:rotateXYZ"), fromXYZ },
        { TfToken("xformOp:rotateXZY"), fromXZY }, { TfToken("xformOp:rotateYXZ"), fromYXZ },
        { TfToken("xformOp:rotateYZX"), fromYZX }, { TfToken("xformOp:rotateZXY"), fromZXY },
        { TfToken("xformOp:rotateZYX"), fromZYX }, { TfToken("xformOp:orient"), nullptr }
    }; // FIXME, unsupported.

    return cvt.at(opName);
}

UsdTransform3dMayaXformStack::CvtRotXYZToAttrFn
UsdTransform3dMayaXformStack::getCvtRotXYZToAttrFn(const TfToken& opName) const
{
    static std::unordered_map<TfToken, CvtRotXYZToAttrFn, TfToken::HashFunctor> cvt = {
        { TfToken("xformOp:rotateX"), toX },     { TfToken("xformOp:rotateY"), toY },
        { TfToken("xformOp:rotateZ"), toZ },     { TfToken("xformOp:rotateXYZ"), toXYZ },
        { TfToken("xformOp:rotateXZY"), toXZY }, { TfToken("xformOp:rotateYXZ"), toYXZ },
        { TfToken("xformOp:rotateYZX"), toYZX }, { TfToken("xformOp:rotateZXY"), toZXY },
        { TfToken("xformOp:rotateZYX"), toZYX }, { TfToken("xformOp:orient"), nullptr }
    }; // FIXME, unsupported.

    return cvt.at(opName);
}

bool UsdTransform3dMayaXformStack::isAttributeEditAllowed(
    const PXR_NS::TfToken attrName,
    std::string&          errMsg) const
{
    PXR_NS::UsdAttribute attr;
    if (!attrName.IsEmpty())
        attr = prim().GetAttribute(attrName);
    if (attr && !UsdUfe::isAttributeEditAllowed(attr, &errMsg)) {
        return false;
    } else if (!attr) {
        UsdGeomXformable xformable(prim());
        if (!UsdUfe::isAttributeEditAllowed(xformable.GetXformOpOrderAttr(), &errMsg)) {
            return false;
        }
    }
    return true;
}

//------------------------------------------------------------------------------
// UsdTransform3dMayaXformStackHandler
//------------------------------------------------------------------------------

UsdTransform3dMayaXformStackHandler::UsdTransform3dMayaXformStackHandler(
    const Ufe::Transform3dHandler::Ptr& nextHandler)
    : Ufe::Transform3dHandler()
    , _nextHandler(nextHandler)
{
}

/*static*/
UsdTransform3dMayaXformStackHandler::Ptr
UsdTransform3dMayaXformStackHandler::create(const Ufe::Transform3dHandler::Ptr& nextHandler)
{
    return std::make_shared<UsdTransform3dMayaXformStackHandler>(nextHandler);
}

Ufe::Transform3d::Ptr
UsdTransform3dMayaXformStackHandler::transform3d(const Ufe::SceneItem::Ptr& item) const
{
    return createTransform3d(item, [&]() { return _nextHandler->transform3d(item); });
}

Ufe::Transform3d::Ptr UsdTransform3dMayaXformStackHandler::editTransform3d(
    const Ufe::SceneItem::Ptr&      item,
    const Ufe::EditTransform3dHint& hint) const
{
    // MAYA-109190: Moved the IsInstanceProxy() check here since it was causing the
    // camera framing not properly be applied.
    //
    // HS January 15, 2021: After speaking with Pierre, there is a more robust solution to move this
    // check entirely from here.

    // According to USD docs, editing scene description via instance proxies and their properties is
    // not allowed.
    // https://graphics.pixar.com/usd/docs/api/_usd__page__scenegraph_instancing.html#Usd_ScenegraphInstancing_InstanceProxies
    UsdSceneItem::Ptr usdItem = std::dynamic_pointer_cast<UsdSceneItem>(item);
    if (usdItem->prim().IsInstanceProxy()) {
        MGlobal::displayError(
            MString("Authoring to the descendant of an instance [")
            + MString(usdItem->prim().GetName().GetString().c_str()) + MString("] is not allowed. ")
            + MString("Please mark 'instanceable=false' to author edits to instance proxies."));
        return nullptr;
    }

    std::string errMsg;
    if (!UsdUfe::isEditTargetLayerModifiable(usdItem->prim().GetStage(), &errMsg)) {
        MGlobal::displayError(errMsg.c_str());
        return nullptr;
    }

    return createTransform3d(item, [&]() { return _nextHandler->editTransform3d(item, hint); });
}

} // namespace ufe
} // namespace MAYAUSD_NS_DEF
