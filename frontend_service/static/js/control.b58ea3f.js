/*! For license information please see control.b58ea3f.js.LICENSE.txt */
(window.webpackJsonp = window.webpackJsonp || []).push([[2], {
    987: function(t, e, o) {
        t.exports = o.p + "static/img/rhythm.d2a3364.gif"
    },
    988: function(t, e, o) {
        t.exports = o.p + "static/img/rhythm_static.d799831.jpg"
    },
    996: function(t, e, o) {
        "use strict";
        o.r(e);
        var r = {
            xarm: ["gripper", "suctioncup", "bio-gripper", "robotiq-2F-85", "robotiq-2F-140", "otherCylinder", "otherCuboid"],
            lite6: ["gripper", "suctioncup", "otherCylinder", "otherCuboid"]
        }
          , n = {
            "": 0,
            gripper: 1,
            suctioncup: 2,
            "bio-gripper": 3,
            "robotiq-2F-85": 4,
            "robotiq-2F-140": 5,
            otherCylinder: 21,
            otherCuboid: 22
        }
          , i = {
            name: "SetBaudRateDialog",
            props: {
                visible: {
                    type: Boolean
                },
                type: {
                    type: String,
                    require: !0
                },
                end_baud_rate: {
                    type: Number,
                    require: !0
                }
            },
            data: function() {
                return {
                    xarmLang: window.location.search.includes("lang=cn") ? "cn" : "en"
                }
            },
            methods: {
                closeDialog: function() {
                    this.$emit("update:visible", !1)
                },
                setModbusBaudRate: function() {
                    window.CommandsSettingSocket.setModbusBaudrate(this.endDefaultBaudRate, (function(t) {}
                    )),
                    this.closeDialog()
                }
            },
            computed: {
                endDefaultBaudRate: function() {
                    return ["gripper", "bio-gripper"].includes(this.type) ? 2e6 : ["robotiq-2F-85", "robotiq-2F-140"].includes(this.type) ? 115200 : null
                },
                tips: function() {
                    return "cn" === this.xarmLang ? "机械臂的波特率为 ".concat(this.end_baud_rate, ", ").concat(this.type, "的波特率为\n        ").concat(this.endDefaultBaudRate, "\n是否将机械臂的波特率设置为 ").concat(this.endDefaultBaudRate, " ?") : "The baud rate of the robotic arm is ".concat(this.end_baud_rate, ",\n        and the baud rate of the ").concat(this.type, " is  ").concat(this.endDefaultBaudRate, "\n        \nChange the baud rate of the robotic arm to  ").concat(this.endDefaultBaudRate, " ?")
                }
            }
        }
          , a = o(23)
          , s = Object(a.a)(i, (function() {
            var t = this
              , e = t._self._c;
            return e("el-dialog", {
                staticClass: "uf-dialog un-title",
                attrs: {
                    visible: t.visible,
                    "show-close": !1,
                    "close-on-click-modal": !1,
                    center: "",
                    width: "450px"
                },
                on: {
                    "update:visible": function(e) {
                        t.visible = e
                    }
                }
            }, [e("div", [e("p", [t._v(t._s(t.tips))])]), t._v(" "), e("span", {
                staticClass: "dialog-footer justify-center uf-space",
                attrs: {
                    slot: "footer"
                },
                slot: "footer"
            }, [e("button", {
                staticClass: "uf-button__cancel large",
                on: {
                    click: t.closeDialog
                }
            }, [t._v("\n          " + t._s(t.$t("No")) + "\n        ")]), t._v(" "), e("button", {
                staticClass: "uf-button large",
                on: {
                    click: t.setModbusBaudRate
                }
            }, [t._v("\n          " + t._s(t.$t("Yes")) + "\n        ")])])])
        }
        ), [], !1, null, null, null).exports;
        function c(t, e) {
            return function(t) {
                if (Array.isArray(t))
                    return t
            }(t) || function(t, e) {
                var o = null == t ? null : "undefined" != typeof Symbol && t[Symbol.iterator] || t["@@iterator"];
                if (null != o) {
                    var r, n, i, a, s = [], c = !0, l = !1;
                    try {
                        if (i = (o = o.call(t)).next,
                        0 === e) {
                            if (Object(o) !== o)
                                return;
                            c = !1
                        } else
                            for (; !(c = (r = i.call(o)).done) && (s.push(r.value),
                            s.length !== e); c = !0)
                                ;
                    } catch (t) {
                        l = !0,
                        n = t
                    } finally {
                        try {
                            if (!c && null != o.return && (a = o.return(),
                            Object(a) !== a))
                                return
                        } finally {
                            if (l)
                                throw n
                        }
                    }
                    return s
                }
            }(t, e) || function(t, e) {
                if (!t)
                    return;
                if ("string" == typeof t)
                    return l(t, e);
                var o = Object.prototype.toString.call(t).slice(8, -1);
                "Object" === o && t.constructor && (o = t.constructor.name);
                if ("Map" === o || "Set" === o)
                    return Array.from(t);
                if ("Arguments" === o || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(o))
                    return l(t, e)
            }(t, e) || function() {
                throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method.")
            }()
        }
        function l(t, e) {
            (null == e || e > t.length) && (e = t.length);
            for (var o = 0, r = new Array(e); o < e; o++)
                r[o] = t[o];
            return r
        }
        var u = {
            name: "SetGeometryDialog",
            i18n: {
                messages: {
                    en: {
                        otherCylinder: "Cylinder",
                        otherCuboid: "Cuboid",
                        cancel: "Cancel",
                        save: "Save",
                        pleaseFillIn: "Please fill in "
                    },
                    cn: {
                        otherCylinder: "圆柱体",
                        otherCuboid: "长方体",
                        cancel: "取消",
                        save: "保存",
                        pleaseFillIn: "请填写"
                    }
                }
            },
            props: {
                visible: {
                    type: Boolean
                },
                type: {
                    type: String,
                    require: !0
                },
                data: {
                    require: !0
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    xarmLang: window.location.search.includes("lang=cn") ? "cn" : "en",
                    cuboidParameter: [0, 0, 0, 0, 0, 0],
                    cylinderParameter: [0, 0, 0, 0, 0, 0]
                }
            },
            methods: {
                closeDialog: function() {
                    this.$emit("update:visible", !1)
                },
                setDefaultData: function() {
                    "otherCuboid" === this.type ? this.cuboidParameter = this.data : this.cylinderParameter = this.data
                },
                onSetCubeSize: function() {
                    var t = n[this.type || ""];
                    if (21 === t) {
                        var e = c(this.cylinderParameter, 2)
                          , o = e[0]
                          , r = void 0 === o ? 0 : o
                          , i = e[1];
                        if ([r || 0, (void 0 === i ? 0 : i) || 0].includes(0))
                            return this.$message.warning("".concat(this.$t("pleaseFillIn"), "R,H"))
                    }
                    if (22 === t) {
                        var a = c(this.cuboidParameter, 3)
                          , s = a[0]
                          , l = void 0 === s ? 0 : s
                          , u = a[1]
                          , d = void 0 === u ? 0 : u
                          , p = a[2];
                        if ([l || 0, d || 0, (void 0 === p ? 0 : p) || 0].includes(0))
                            return this.$message.warning("".concat(this.$t("pleaseFillIn"), "Lx,Ly,Lz"))
                    }
                    var m = "otherCuboid" === this.type ? [1, 22, this.cuboidParameter] : [1, 21, this.cylinderParameter];
                    return this.$parent.setEndEffector(m),
                    this.closeDialog(),
                    !1
                }
            },
            computed: {
                cubeImage: function() {
                    return this.$store.getters.isLite6 ? "otherCylinder" === this.type ? "model-cylinder-lite6" : "model-cuboid-lite6" : this.$store.getters.is1300Arm ? "otherCylinder" === this.type ? "model-cylinder-xarm1300" : "model-cuboid-xarm1300" : "otherCylinder" === this.type ? "model-cylinder-xarm1200" : "model-cuboid-xarm1200"
                }
            },
            watch: {
                visible: function() {
                    this.setDefaultData()
                }
            }
        }
          , d = {
            name: "GripperSetting",
            components: {
                SetGeometryDialog: Object(a.a)(u, (function() {
                    var t = this
                      , e = t._self._c;
                    return e("el-dialog", {
                        staticClass: "uf-dialog un-title",
                        attrs: {
                            visible: t.visible,
                            "show-close": !1,
                            "close-on-click-modal": !1,
                            center: "",
                            width: "40%"
                        },
                        on: {
                            "update:visible": function(e) {
                                t.visible = e
                            }
                        }
                    }, [e("div", {
                        attrs: {
                            slot: "title"
                        },
                        slot: "title"
                    }, [t._v("\n    " + t._s(t.$t(t.type)) + "\n  ")]), t._v(" "), e("div", {
                        staticClass: "hpx-280"
                    }, [e("div", [e("div", {
                        staticClass: "mb-px-20",
                        class: t.cubeImage
                    }), t._v(" "), "otherCuboid" === t.type ? e("div", {
                        staticClass: "other-parameter-wrapper row-fit-3"
                    }, [e("div", {
                        staticClass: "box font-vw-20"
                    }, [e("span", [t._v("Lx")]), t._v(" "), e("input", {
                        directives: [{
                            name: "model",
                            rawName: "v-model.number",
                            value: t.cuboidParameter[0],
                            expression: "cuboidParameter[0]",
                            modifiers: {
                                number: !0
                            }
                        }],
                        staticClass: "un-spin",
                        attrs: {
                            type: "number"
                        },
                        domProps: {
                            value: t.cuboidParameter[0]
                        },
                        on: {
                            input: function(e) {
                                e.target.composing || t.$set(t.cuboidParameter, 0, t._n(e.target.value))
                            },
                            blur: function(e) {
                                return t.$forceUpdate()
                            }
                        }
                    }), t._v(" "), e("span", [t._v("mm")])]), t._v(" "), e("div", {
                        staticClass: "box font-vw-20"
                    }, [e("span", [t._v("Ly")]), t._v(" "), e("input", {
                        directives: [{
                            name: "model",
                            rawName: "v-model.number",
                            value: t.cuboidParameter[1],
                            expression: "cuboidParameter[1]",
                            modifiers: {
                                number: !0
                            }
                        }],
                        staticClass: "un-spin",
                        attrs: {
                            type: "number"
                        },
                        domProps: {
                            value: t.cuboidParameter[1]
                        },
                        on: {
                            input: function(e) {
                                e.target.composing || t.$set(t.cuboidParameter, 1, t._n(e.target.value))
                            },
                            blur: function(e) {
                                return t.$forceUpdate()
                            }
                        }
                    }), t._v(" "), e("span", [t._v("mm")])]), t._v(" "), e("div", {
                        staticClass: "box font-vw-20"
                    }, [e("span", [t._v("Lz")]), t._v(" "), e("input", {
                        directives: [{
                            name: "model",
                            rawName: "v-model.number",
                            value: t.cuboidParameter[2],
                            expression: "cuboidParameter[2]",
                            modifiers: {
                                number: !0
                            }
                        }],
                        staticClass: "un-spin",
                        attrs: {
                            type: "number"
                        },
                        domProps: {
                            value: t.cuboidParameter[2]
                        },
                        on: {
                            input: function(e) {
                                e.target.composing || t.$set(t.cuboidParameter, 2, t._n(e.target.value))
                            },
                            blur: function(e) {
                                return t.$forceUpdate()
                            }
                        }
                    }), t._v(" "), e("span", [t._v("mm")])])]) : t._e(), t._v(" "), "otherCylinder" === t.type ? e("div", {
                        staticClass: "other-parameter-wrapper row-fit-2"
                    }, [e("div", {
                        staticClass: "box font-vw-20"
                    }, [e("span", [t._v("R")]), t._v(" "), e("input", {
                        directives: [{
                            name: "model",
                            rawName: "v-model.number",
                            value: t.cylinderParameter[0],
                            expression: "cylinderParameter[0]",
                            modifiers: {
                                number: !0
                            }
                        }],
                        staticClass: "un-spin",
                        attrs: {
                            type: "number"
                        },
                        domProps: {
                            value: t.cylinderParameter[0]
                        },
                        on: {
                            input: function(e) {
                                e.target.composing || t.$set(t.cylinderParameter, 0, t._n(e.target.value))
                            },
                            blur: function(e) {
                                return t.$forceUpdate()
                            }
                        }
                    }), t._v(" "), e("span", [t._v("mm")])]), t._v(" "), e("div", {
                        staticClass: "box font-vw-20"
                    }, [e("span", [t._v(" H")]), t._v(" "), e("input", {
                        directives: [{
                            name: "model",
                            rawName: "v-model.number",
                            value: t.cylinderParameter[1],
                            expression: "cylinderParameter[1]",
                            modifiers: {
                                number: !0
                            }
                        }],
                        staticClass: "un-spin",
                        attrs: {
                            type: "number"
                        },
                        domProps: {
                            value: t.cylinderParameter[1]
                        },
                        on: {
                            input: function(e) {
                                e.target.composing || t.$set(t.cylinderParameter, 1, t._n(e.target.value))
                            },
                            blur: function(e) {
                                return t.$forceUpdate()
                            }
                        }
                    }), t._v(" "), e("span", [t._v("mm")])])]) : t._e()])]), t._v(" "), e("span", {
                        staticClass: "dialog-footer justify-center uf-space",
                        attrs: {
                            slot: "footer"
                        },
                        slot: "footer"
                    }, [e("button", {
                        staticClass: "uf-button__cancel large",
                        on: {
                            click: t.closeDialog
                        }
                    }, [t._v("\n          " + t._s(t.$t("cancel")) + "\n        ")]), t._v(" "), e("button", {
                        staticClass: "uf-button large",
                        on: {
                            click: t.onSetCubeSize
                        }
                    }, [t._v("\n          " + t._s(t.$t("save")) + "\n        ")])])])
                }
                ), [], !1, null, "1e2d2bd1", null).exports
            },
            i18n: {
                messages: {
                    en: {
                        notEnd: "Select an end effector",
                        suctionCup: "xArm Vacuum Gripper",
                        suctionCupLite6: "Vacuum Gripper",
                        bioGripper: "xArm BIO Gripper",
                        open: "OPEN",
                        close: "CLOSE",
                        openClaw: "OPEN",
                        closeClaw: "CLOSE",
                        off: "OFF",
                        compensationLoad: "Compensation load",
                        isLoadTips: "Compensation load failed, please try again!",
                        enable: "Enable",
                        enabled: "Enabled",
                        confirm: "Confirm",
                        edit: "Edit"
                    },
                    cn: {
                        notEnd: "选择一个末端执行器",
                        suctionCup: "xArm真空吸头",
                        suctionCupLite6: "真空吸头",
                        bioGripper: "xArm BIO机械爪",
                        open: "打开",
                        close: "关闭",
                        openClaw: "张开",
                        closeClaw: "闭合",
                        off: "关闭",
                        compensationLoad: "补偿负载",
                        isLoadTips: "补偿负载失败，请重试！",
                        enable: "使能",
                        enabled: "已使能",
                        confirm: "确定",
                        edit: "编辑"
                    }
                }
            },
            props: {
                type: {
                    type: String,
                    require: !0
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    showCubeDialog: !1
                }
            },
            created: function() {},
            methods: {
                initBioGripper: function(t) {
                    this.status = t,
                    window.CommandsRobotSocket.set_bio_gripper_enable(!0)
                },
                initRobotiqGripper: function() {
                    window.CommandsRobotSocket.set_robotiq_init()
                },
                setEndEffectorStatus: function(t) {
                    if (this.$store.getters.isLite6) {
                        var e = "close";
                        return "stop" === t ? e = "stop" : !0 === t && (e = "open"),
                        void window.CommandsRobotSocket.setLite6Gripper(e, !1, (function(t) {}
                        ))
                    }
                    "suctioncup" === this.endToolType ? window.CommandsRobotSocket.setSuctionCup(t, !1, (function(t) {}
                    )) : window.CommandsRobotSocket.set_bio_gripper_position(t ? 130 : 50, this.remoteXArmBioGripperParams.speed, !1, (function(t) {}
                    ))
                },
                onSetGripperPos: function(t) {
                    this.gripper.pos ? this.gripper.pos = +t.target.value : this.gripper.pos = this.gripperRange.pos.min,
                    this.gripper.pos > this.gripperRange.pos.max && (this.gripper.pos = +this.gripperRange.pos.max),
                    this.gripper.pos < this.gripperRange.pos.min && (this.gripper.pos = +this.gripperRange.pos.min),
                    this.setGripperPos()
                },
                setGripperPos: function(t, e) {
                    var o = this;
                    this.sameCount = 0,
                    this.isSetPos = !0,
                    this.isDown = !1,
                    "robotiq-2F-85" === this.endToolType || "robotiq-2F-140" === this.endToolType ? window.CommandsRobotSocket.set_robotiq_position(this.gripper.pos, this.gripper.speed, this.gripper.force, !1, (function(t) {
                        if (o.isSetPos = !1,
                        0 === t.code && null === o.gripperInterval) {
                            var e = o;
                            o.gripperInterval = setInterval((function() {
                                e.getGripperPos()
                            }
                            ), 500)
                        }
                    }
                    )) : window.CommandsRobotSocket.setGripperPosition(this.gripper.pos, this.gripper.speed, !1, (function(t) {
                        if (o.isSetPos = !1,
                        0 === t.code && null === o.gripperInterval) {
                            var e = o;
                            o.gripperInterval = setInterval((function() {
                                e.getGripperPos()
                            }
                            ), 500)
                        }
                    }
                    ))
                },
                setEndEffector: function(t) {
                    return this.$parent.setEndEffector(t),
                    !1
                },
                onSetCube: function() {
                    this.showCubeDialog = !0
                }
            },
            computed: {
                remoteXArmBioGripperParams: function() {
                    return this.model.robot.state.local.biogripper
                },
                endToolType: function() {
                    var t = this.model.robot.state.info.xarm_end_tool_type;
                    return "other" === t ? 21 === this.xarmSelfCollisionParams[1] ? "otherCylinder" : "otherCuboid" : t
                },
                bioGripperIsEnabled: function() {
                    var t = this.model.robot.state.info.bio_gripper_is_enabled;
                    return "bio-gripper" === this.endToolType && !t
                },
                showOperationSwitch: function() {
                    var t = ["gripper", "robotiq-2F-85", "robotiq-2F-140"].includes(this.endToolType);
                    return !(!this.$store.getters.isLite6 || !t) || !t
                },
                showObjectSize: function() {
                    return ["otherCylinder", "otherCuboid"].includes(this.endToolType)
                },
                lite6Gripper: function() {
                    return "gripper" === this.endToolType && this.$store.getters.isLite6
                },
                hasEnabledGripper: function() {
                    return ["robotiq-2F-85", "robotiq-2F-140"].includes(this.endToolType)
                },
                gripper: function() {
                    var t = this.model.robot.state.local
                      , e = t.robotiq
                      , o = t.gripper;
                    return this.hasEnabledGripper ? e : o
                },
                remoteGripper: function() {
                    var t = this.model.robot.state.remote
                      , e = t.robotiq
                      , o = t.gripper;
                    return this.hasEnabledGripper ? e : o
                },
                gripperRange: function() {
                    var t = this.model.robot.state.range
                      , e = t.robotiq
                      , o = t.gripper;
                    return this.hasEnabledGripper ? e : o
                },
                xarmSelfCollisionParams: function() {
                    return this.model.robot.state.remote.xarmSelfCollisionParams
                },
                xarmOtherCylinderParams: function() {
                    return this.model.robot.state.remote.xarmOtherCylinderParams
                },
                xarmOtherCuboidParams: function() {
                    return this.model.robot.state.remote.xarmOtherCuboidParams
                }
            }
        }
          , p = {
            name: "EndEffector",
            components: {
                GripperSetting: Object(a.a)(d, (function() {
                    var t = this
                      , e = t._self._c;
                    return t.$store.getters.isSimulationMode ? t._e() : e("div", {
                        staticClass: "gripper-setting"
                    }, [t.endToolType ? t.showObjectSize ? e("div", {
                        staticClass: "flex justify-between"
                    }, ["otherCuboid" === t.endToolType ? e("div", {
                        staticClass: "other-parameter-wrapper"
                    }, [e("div", {
                        staticClass: "box font-vw-18"
                    }, [e("span", {
                        staticClass: "mr-px-5"
                    }, [t._v("Lx ")]), t._v(" "), e("span", [t._v(t._s(t.xarmOtherCuboidParams[0].toFixed(0)))])]), t._v(" "), e("div", {
                        staticClass: "box font-vw-18"
                    }, [e("span", {
                        staticClass: "mr-px-5"
                    }, [t._v("Ly ")]), t._v(" "), e("span", [t._v(t._s(t.xarmOtherCuboidParams[1].toFixed(0)))])]), t._v(" "), e("div", {
                        staticClass: "box font-vw-18"
                    }, [e("span", {
                        staticClass: "mr-px-5"
                    }, [t._v("Lz ")]), t._v(" "), e("span", [t._v(t._s(t.xarmOtherCuboidParams[2].toFixed(0)))])])]) : t._e(), t._v(" "), "otherCylinder" === t.endToolType ? e("div", {
                        staticClass: "other-parameter-wrapper"
                    }, [e("div", {
                        staticClass: "box font-vw-18"
                    }, [e("span", {
                        staticClass: "mr-px-5"
                    }, [t._v("R ")]), t._v(" "), e("span", [t._v(t._s(t.xarmOtherCylinderParams[0].toFixed(0)))])]), t._v(" "), e("div", {
                        staticClass: "box font-vw-18"
                    }, [e("span", {
                        staticClass: "mr-px-5"
                    }, [t._v(" H ")]), t._v(" "), e("span", [t._v(t._s(t.xarmOtherCylinderParams[1].toFixed(0)))])])]) : t._e(), t._v(" "), e("button", {
                        staticClass: "uf-button small confirm-btn",
                        on: {
                            click: t.onSetCube
                        }
                    }, [t._v("\n      " + t._s(t.$t("edit")) + "\n    ")])]) : t.showOperationSwitch ? e("div", [e("div", {
                        staticClass: "flex justify-between"
                    }, [e("div", {
                        staticClass: "flex uf-space"
                    }, [e("button", {
                        staticClass: "uf-button small",
                        attrs: {
                            disabled: t.bioGripperIsEnabled
                        },
                        on: {
                            click: () => t.setEndEffectorStatus(!0)
                        }
                    }, [t._v("\n          " + t._s(t.lite6Gripper ? t.$t("openClaw") : t.$t("open")) + "\n        ")]), t._v(" "), e("button", {
                        staticClass: "uf-button small",
                        attrs: {
                            disabled: t.bioGripperIsEnabled
                        },
                        on: {
                            click: () => t.setEndEffectorStatus(!1)
                        }
                    }, [t._v("\n          " + t._s(t.lite6Gripper ? t.$t("closeClaw") : t.$t("close")) + "\n        ")]), t._v(" "), t.lite6Gripper ? e("button", {
                        staticClass: "uf-button small",
                        attrs: {
                            disabled: t.bioGripperIsEnabled
                        },
                        on: {
                            click: () => t.setEndEffectorStatus("stop")
                        }
                    }, [t._v("\n          " + t._s(t.$t("off")) + "\n        ")]) : t._e()]), t._v(" "), "suctioncup" === t.endToolType || t.$store.getters.isLite6 ? t._e() : e("div", {
                        staticClass: "uf-button small",
                        on: {
                            click: function(e) {
                                return t.initBioGripper("enable")
                            }
                        }
                    }, [t._v("\n        " + t._s(t.$t("enable")) + "\n      ")])])]) : e("div", [e("div", {
                        staticClass: "flex mb-px-20",
                        class: t.hasEnabledGripper ? "justify-between" : "justify-end"
                    }, [t.hasEnabledGripper ? e("button", {
                        staticClass: "uf-button small",
                        on: {
                            click: t.initRobotiqGripper
                        }
                    }, [t._v("\n        " + t._s(t.$t("enable")) + "\n      ")]) : t._e(), t._v(" "), e("el-input", {
                        staticClass: "uf-input uf-input unspin uf-txt-center w-30 set-pos",
                        attrs: {
                            type: "number"
                        },
                        on: {
                            blur: t.onSetGripperPos
                        },
                        model: {
                            value: t.gripper.pos,
                            callback: function(e) {
                                t.$set(t.gripper, "pos", e)
                            },
                            expression: "gripper.pos"
                        }
                    })], 1), t._v(" "), e("el-slider", {
                        staticClass: "uf-slider un-margin",
                        attrs: {
                            max: t.gripperRange.pos.max,
                            min: t.gripperRange.pos.min,
                            step: 1
                        },
                        on: {
                            change: t.setGripperPos
                        },
                        model: {
                            value: t.gripper.pos,
                            callback: function(e) {
                                t.$set(t.gripper, "pos", e)
                            },
                            expression: "gripper.pos"
                        }
                    })], 1) : e("div", {
                        staticClass: "end-less font-vw-14"
                    }, [t._v("\n    " + t._s(t.$t("notEnd")) + "\n  ")]), t._v(" "), e("SetGeometryDialog", {
                        attrs: {
                            visible: t.showCubeDialog,
                            data: "otherCuboid" === t.endToolType ? this.xarmOtherCuboidParams : this.xarmOtherCylinderParams,
                            type: t.endToolType
                        },
                        on: {
                            "update:visible": function(e) {
                                t.showCubeDialog = e
                            }
                        }
                    })], 1)
                }
                ), [], !1, null, "420c00ab", null).exports,
                SetBaudRateDialog: s
            },
            i18n: {
                messages: {
                    en: {
                        title: "Select an End Effector for the robot",
                        noEffector: "No End Effector",
                        gripper: "Gripper",
                        suctioncup: "Vacuum Gripper",
                        otherCylinder: "Cylinder",
                        otherCuboid: "Cuboid",
                        "bio-gripper": "xArm BIO Gripper",
                        "robotiq-2F-85": "Robotiq-2F-85 Gripper",
                        "robotiq-2F-140": "Robotiq-2F-140 Gripper",
                        confirm: "Save",
                        yes1: "Yes",
                        no1: "No",
                        pleaseFillIn: "Please fill in ",
                        enable: "enable"
                    },
                    cn: {
                        title: "选择末端执行器",
                        noEffector: "无末端执行器",
                        gripper: "机械爪",
                        suctioncup: "真空吸头",
                        otherCylinder: "圆柱体",
                        otherCuboid: "长方体",
                        "bio-gripper": "xArm BIO机械爪",
                        "robotiq-2F-85": "Robotiq-2F-85机械爪",
                        "robotiq-2F-140": "Robotiq-2F-140机械爪",
                        confirm: "确定",
                        yes1: "是",
                        no1: "否",
                        pleaseFillIn: "请填写",
                        enable: "使能"
                    }
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    localEndEffector: "suctioncup",
                    cylinderParameter: [0, 0, 0, 0, 0, 0],
                    cuboidParameter: [0, 0, 0, 0, 0, 0],
                    isShowBaudRateDialog: !1,
                    end_baud_rate: 0
                }
            },
            mounted: function() {
                this.localEndEffector = "suctioncup"
            },
            methods: {
                setEndEffector: function() {
                    var t = this
                      , e = arguments.length > 0 && void 0 !== arguments[0] ? arguments[0] : null
                      , o = n["suctioncup"]
                      , r = [1, o, [0, 0, 0, 0, 0, 0]]
                      , i = "suctioncup";
                    return [21, 22].includes(o) && (r = "otherCuboid" === i ? [1, 22, this.xarmOtherCuboidParams] : [1, 21, this.xarmOtherCylinderParams],
                    e && (r = e)),
                    this.remoteSelfCollision ? window.CommandsRobotSocket.setEndType(i, !0, r, (function(e) {
                        window.CommandsRobotSocket.save_config((function(t) {}
                        )),
                        0 === e.code && (t.isShowBaudRateDialog = e.data.check_res,
                        t.end_baud_rate = e.data.end_baudrate)
                    }
                    )) : window.CommandsRobotSocket.setEndType(i, !0, r),
                    !1
                },
                clearError: function() {
                    var t = this;
                    this.$store.getters.armNotEnabled && window.CommandsRobotSocket.cleanErrorWarn((function(e) {
                        t.showErrDialog()
                    }
                    ))
                }
            },
            computed: {
                xarmSelfCollisionParams: function() {
                    return this.model.robot.state.remote.xarmSelfCollisionParams
                },
                effectorOptions: function() {
                    var t = this
                      , e = this.$store.getters.isLite6 ? "lite6" : "xarm"
                      , o = r[e].map((function(e) {
                        return {
                            label: t.$t(e),
                            value: e
                        }
                    }
                    ));
                    return o.unshift({
                        label: this.$t("noEffector"),
                        value: ""
                    }),
                    o
                },
                remoteEndEffector: function() {
                    return "suctioncup"
                },
                remoteSelfCollision: function() {
                    return 1 === this.model.robot.state.remote.xarmSelfCollisionParams[0]
                },
                xarmOtherCylinderParams: function() {
                    return this.model.robot.state.remote.xarmOtherCylinderParams
                },
                xarmOtherCuboidParams: function() {
                    return this.model.robot.state.remote.xarmOtherCuboidParams
                }
            },
            watch: {
                remoteEndEffector: function(t) {
                    this.localEndEffector = "suctioncup"
                }
            }
        }
          , m = p
          , f = Object(a.a)(m, (function() {
            var t = this
              , e = t._self._c;
            return e("div", {
                staticClass: "end-effector",
                class: t.localEndEffector ? "has-tool" : ""
            }, [e("el-select", {
                staticClass: "uf-select indent effector-select uf-txt-subtitle",
                on: {
                    change: () => t.setEndEffector()
                },
                model: {
                    value: "suctioncup",
                    callback: function(e) {
                        t.localEndEffector = "suctioncup"
                    },
                    expression: "localEndEffector"
                }
            }, t._l(t.effectorOptions, (function(t) {
                return e("el-option", {
                    key: t.value,
                    attrs: {
                        label: t.label,
                        value: t.value
                    }
                })
            }
            )), 1), t._v(" "), e("GripperSetting"), t._v(" "), e("SetBaudRateDialog", {
                attrs: {
                    visible: t.isShowBaudRateDialog,
                    end_baud_rate: t.end_baud_rate,
                    type: "suctioncup"
                },
                on: {
                    "update:visible": function(e) {
                        t.isShowBaudRateDialog = e
                    }
                }
            })], 1)
        }
        ), [], !1, null, "5372d100", null).exports
          , h = o(461)
          , v = o(48)
          , b = o(398);
        function g(t) {
            return g = "function" == typeof Symbol && "symbol" == typeof Symbol.iterator ? function(t) {
                return typeof t
            }
            : function(t) {
                return t && "function" == typeof Symbol && t.constructor === Symbol && t !== Symbol.prototype ? "symbol" : typeof t
            }
            ,
            g(t)
        }
        function y(t, e) {
            var o = Object.keys(t);
            if (Object.getOwnPropertySymbols) {
                var r = Object.getOwnPropertySymbols(t);
                e && (r = r.filter((function(e) {
                    return Object.getOwnPropertyDescriptor(t, e).enumerable
                }
                ))),
                o.push.apply(o, r)
            }
            return o
        }
        function _(t) {
            for (var e = 1; e < arguments.length; e++) {
                var o = null != arguments[e] ? arguments[e] : {};
                e % 2 ? y(Object(o), !0).forEach((function(e) {
                    w(t, e, o[e])
                }
                )) : Object.getOwnPropertyDescriptors ? Object.defineProperties(t, Object.getOwnPropertyDescriptors(o)) : y(Object(o)).forEach((function(e) {
                    Object.defineProperty(t, e, Object.getOwnPropertyDescriptor(o, e))
                }
                ))
            }
            return t
        }
        function w(t, e, o) {
            var r;
            return r = function(t, e) {
                if ("object" != g(t) || !t)
                    return t;
                var o = t[Symbol.toPrimitive];
                if (void 0 !== o) {
                    var r = o.call(t, e || "default");
                    if ("object" != g(r))
                        return r;
                    throw new TypeError("@@toPrimitive must return a primitive value.")
                }
                return ("string" === e ? String : Number)(t)
            }(e, "string"),
            (e = "symbol" == g(r) ? r : r + "")in t ? Object.defineProperty(t, e, {
                value: o,
                enumerable: !0,
                configurable: !0,
                writable: !0
            }) : t[e] = o,
            t
        }
        var C = {
            name: "TrajectoryList",
            props: {
                visible: {
                    type: Boolean
                },
                trajectoryList: {
                    type: Array
                },
                curTrajectory: {
                    type: String
                }
            },
            i18n: {
                messages: {
                    en: {
                        myApp: "My Projects",
                        import: "Import Project",
                        uploadSuccess: "Upload success",
                        uploadFailed: "Upload failed, content error",
                        uploadUnCompatible: "Engineering is not compatible",
                        uploadFmtLimit: "Only supports .gz or .xml format",
                        uploadSizeLimit: "Upload size can not over 10M",
                        downloadSuccess: "Download success",
                        downloadFailed: "Download failed",
                        runningTip: "Project is running!",
                        ID: "ID",
                        title: "Title",
                        operate: "Select",
                        convertFailed: "Convert failed",
                        duration: "Duration",
                        deleteTip: "Delete Tip",
                        deleteBtn: "Delete",
                        deleteText: "Are you sure to delete this project?",
                        deleteSuccess: "Delete success！",
                        deleteFailed: "Delete failed!",
                        cancelBtn: "Cancel",
                        deselectAll: "Deselect All",
                        selectAll: "Select All",
                        unselectText: "Cancel",
                        importText: "Import",
                        selectText: "Select",
                        export: "Export",
                        delete: "Delete"
                    },
                    cn: {
                        myApp: "我的项目",
                        import: "导入项目",
                        uploadSuccess: "上传成功",
                        uploadFailed: "上传失败, 内容不对",
                        uploadUnCompatible: "上传的工程与机械臂不兼容",
                        uploadFmtLimit: "只支持.gz或.xml格式",
                        uploadSizeLimit: "上传大小不能超过10M",
                        downloadSuccess: "下载成功",
                        downloadFailed: "下载失败",
                        runningTip: "项目正在运行!",
                        ID: "序号",
                        title: "名字",
                        operate: "选择",
                        convertFailed: "转换失败",
                        duration: "时长",
                        deleteTip: "删除提示",
                        deleteBtn: "删除",
                        deleteText: "是否确认删除选中项目",
                        deleteSuccess: "删除成功！",
                        deleteFailed: "删除失败",
                        cancelBtn: "取消",
                        deselectAll: "取消全选",
                        selectAll: "全选",
                        export: "导出",
                        unselectText: "取消",
                        importText: "导入",
                        selectText: "选择",
                        delete: "删除"
                    }
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    constant: window.GlobalConstant,
                    uploadUrl: "http://".concat(window.GlobalUtil.socketInfo.host, "/project/upload"),
                    enableSelection: !1,
                    curInputData: {},
                    isDblClick: !1,
                    editId: "",
                    isOnEnter: !1
                }
            },
            methods: {
                closeDialog: function() {
                    this.$emit("update:visible", !1)
                },
                formatSeconds: v.f,
                getTrajectoryList: function() {
                    var t = this;
                    window.CommandsTrajSocket.listTrajs((function(e) {
                        if (0 === e.code) {
                            var o = e.data.map((function(t) {
                                return _(_({}, t), {}, {
                                    selected: !1
                                })
                            }
                            ));
                            t.$emit("update:trajectoryList", o)
                        }
                    }
                    ))
                },
                onSelectionItem: function(t, e) {
                    this.enableSelection && (console.log("onSelectionItem", t),
                    t.selected = !t.selected,
                    this.$set(this.trajectoryList, e, t))
                },
                cancelSelect: function() {
                    var t = this.trajectoryList.map((function(t) {
                        return t.selected = !1,
                        t
                    }
                    ));
                    this.enableSelection = !1,
                    this.$emit("update:trajectoryList", t)
                },
                selectAll: function() {
                    var t = !this.isCheckedAll
                      , e = this.trajectoryList.map((function(e) {
                        return e.selected = t,
                        e
                    }
                    ));
                    this.$emit("update:trajectoryList", e)
                },
                openRenameTrajectory: function(t, e) {},
                onRenameEnter: function(t) {
                    13 === window.event.keyCode && (this.onRenameBlur(t),
                    this.isOnEnter = !1)
                },
                onRenameBlur: function(t) {
                    if (console.log("data", t, this.isOnEnter),
                    this.isOnEnter)
                        this.isOnEnter = !1;
                    else {
                        this.isDblClick = !1;
                        var e = this.curInputData.name;
                        if (this.model.commonStatusMgr.warningTitle = v.k ? "警告" : "Warning",
                        this.model.commonStatusMgr.warningDesc = "",
                        t && e) {
                            if (e === t.name)
                                return void (this.editId = "");
                            var o = this.model.utilModel.checkStrIsLegal(e, !0, !0);
                            if (!0 !== o && !1 !== o)
                                return this.model.commonStatusMgr.warningFlag = !0,
                                void (this.model.commonStatusMgr.warningText = o);
                            for (var r = 0; r < this.trajectoryList.length; r++)
                                if (this.trajectoryList[r].name === e)
                                    return this.model.commonStatusMgr.warningFlag = !0,
                                    void (this.model.commonStatusMgr.warningText = v.k ? "所有文件名不能重复！" : "All file names can't be duplicate");
                            this.renameTrajectory(e, t.name)
                        }
                    }
                },
                renameTrajectory: function(t, e) {
                    var o = this;
                    window.CommandsTrajSocket.renameTraj(t, e, (function(r) {
                        0 === r.code && (o.$parent.getTrajectoryList(),
                        o.curTrajectory === e && o.$emit("update:curTrajectory", t))
                    }
                    ))
                },
                deleteTrajectory: function() {
                    var t = this
                      , e = this.trajectoryList.filter((function(t) {
                        return t.selected
                    }
                    ));
                    0 === this.$parent.check() && 0 !== e.length && this.$confirm(this.$t("deleteText"), this.$t("deleteTip"), {
                        confirmButtonText: this.$t("deleteBtn"),
                        cancelButtonText: this.$t("cancelBtn"),
                        type: "warning"
                    }).then((function() {
                        var e = t.selectTrajectory.map((function(t) {
                            return t.name
                        }
                        ));
                        console.log(e),
                        t.$parent.deleteTranscribe(e, (function(e) {
                            t.$message({
                                type: 0 === e.code ? "success" : "error",
                                message: 0 === e.code ? t.$t("deleteSuccess") : t.$t("deleteFailed")
                            })
                        }
                        ))
                    }
                    )).catch((function() {}
                    ))
                },
                downloadProject: function() {
                    var t = this
                      , e = this.selectTrajectory.map((function(t) {
                        return t.name
                    }
                    ));
                    0 !== e.length && e.forEach((function(e) {
                        t.downloadApp(e)
                    }
                    ))
                },
                downloadApp: function(t) {
                    var e = this
                      , o = window.GlobalUtil.socketInfo.host
                      , r = window.GlobalConstant.COMMON_PARAMS
                      , n = r.userId
                      , i = r.version
                      , a = "http://".concat(o, "/project/download?path=").concat(n, "/").concat(i, "/traj&data={}");
                    void 0 !== t ? (a += "&name=" + t,
                    t += ".tar.gz") : (a += "&name=",
                    t = "all.tar.gz"),
                    fetch(a).then((function(o) {
                        o.blob().then((function(o) {
                            var r = document.createElement("a")
                              , n = window.URL.createObjectURL(o)
                              , i = "traj-" + t;
                            r.href = n,
                            r.download = i,
                            r.click(),
                            window.URL.revokeObjectURL(n),
                            e.$message({
                                message: "".concat(e.$t("downloadSuccess"), ": ").concat(i),
                                type: "success",
                                duration: 3e3,
                                showClose: !0
                            }),
                            e.openSelect = !1
                        }
                        )).catch((function(t) {
                            e.$message.error("".concat(e.$t("downloadFailed"), ": ").concat(t))
                        }
                        ))
                    }
                    )).catch((function(t) {
                        e.$message.error("".concat(e.$t("downloadFailed"), ": ").concat(t))
                    }
                    ))
                },
                handleUploadSuccess: function(t, e) {
                    void 0 !== t.success && -2 !== t.success ? -1 !== t.success ? (this.$message.success(this.$t("uploadSuccess")),
                    this.$parent.getTrajectoryList()) : this.$message.error(this.$t("uploadUnCompatible")) : this.$message.error(this.$t("uploadFailed"))
                },
                beforeUpload: function(t) {
                    var e = -1 !== t.type.indexOf("gzip")
                      , o = t.size / 1024 / 1024 < 50;
                    return e || this.$message.error(this.$t("uploadFmtLimit")),
                    o || this.$message.error(this.$t("uploadSizeLimit")),
                    e && o
                }
            },
            computed: {
                selectTrajectory: function() {
                    return this.trajectoryList.filter((function(t) {
                        return !!t.selected
                    }
                    ))
                },
                isCheckedAll: function() {
                    return this.trajectoryList.length === this.selectTrajectory.length
                },
                hasCheck: function() {
                    return this.selectTrajectory.length > 0 && this.enableSelection
                }
            },
            watch: {
                visible: function(t) {}
            }
        }
          , T = Object(a.a)(C, (function() {
            var t = this
              , e = t._self._c;
            return e("el-dialog", {
                staticClass: "uf-dialog",
                attrs: {
                    visible: t.visible,
                    "show-close": !1,
                    "close-on-click-modal": !1,
                    center: "",
                    width: "45vw"
                },
                on: {
                    "update:visible": function(e) {
                        t.visible = e
                    }
                }
            }, [e("div", {
                staticClass: "header flex justify-between",
                attrs: {
                    slot: "title"
                },
                slot: "title"
            }, [e("div", {
                staticClass: "title"
            }, [t._v("\n      " + t._s(t.$t("myApp")) + "\n    ")]), t._v(" "), e("div", {
                staticClass: "uf-space"
            }, [t.hasCheck ? e("button", {
                staticClass: "uf-button small",
                on: {
                    click: t.downloadProject
                }
            }, [e("i", {
                staticClass: "iconfont icon-export"
            }), t._v("\n        " + t._s(t.$t("export")) + "\n      ")]) : t._e(), t._v(" "), t.hasCheck ? e("button", {
                staticClass: "uf-button small",
                on: {
                    click: t.deleteTrajectory
                }
            }, [e("i", {
                staticClass: "iconfont icon-delete"
            }), t._v("\n        " + t._s(t.$t("delete")) + "\n      ")]) : t._e(), t._v(" "), t.enableSelection ? e("button", {
                staticClass: "uf-button small",
                on: {
                    click: t.cancelSelect
                }
            }, [e("i", {
                staticClass: "iconfont icon-cancel"
            }), t._v("\n        " + t._s(t.$t("unselectText")) + "\n      ")]) : t._e(), t._v(" "), t.enableSelection ? e("button", {
                staticClass: "uf-button small",
                on: {
                    click: t.selectAll
                }
            }, [e("i", {
                staticClass: "iconfont icon-check-all"
            }), t._v("\n        " + t._s(t.isCheckedAll ? t.$t("deselectAll") : t.$t("selectAll")) + "\n      ")]) : t._e(), t._v(" "), t.enableSelection ? t._e() : e("el-upload", {
                staticClass: "uf-button small",
                attrs: {
                    data: {
                        path: `${t.constant.COMMON_PARAMS.userId}/${t.constant.COMMON_PARAMS.version}/traj`
                    },
                    action: t.uploadUrl,
                    "show-file-list": !1,
                    "on-success": t.handleUploadSuccess,
                    "before-upload": t.beforeUpload
                }
            }, [e("i", {
                staticClass: "iconfont icon-import"
            }), t._v("\n        " + t._s(t.$t("importText")) + "\n      ")]), t._v(" "), t.enableSelection ? t._e() : e("button", {
                staticClass: "uf-button small",
                on: {
                    click: function(e) {
                        t.enableSelection = !0
                    }
                }
            }, [e("i", {
                staticClass: "iconfont icon-check-tag"
            }), t._v("\n        " + t._s(t.$t("selectText")) + "\n\n      ")])], 1)]), t._v(" "), e("div", {
                staticClass: "body"
            }, [e("div", {
                staticClass: "app-list",
                attrs: {
                    id: "app-list"
                }
            }, [e("div", {
                staticClass: "block-title header"
            }, [t.enableSelection ? e("div", {
                staticClass: "operate"
            }, [t._v(t._s(t.$t("operate")))]) : t._e(), t._v(" "), e("div", {
                staticClass: "id"
            }, [t._v(t._s(t.$t("ID")))]), t._v(" "), e("div", {
                staticClass: "name-title"
            }, [t._v(t._s(t.$t("title")))]), t._v(" "), e("div", {
                staticClass: "duration duration-clear"
            }, [t._v(t._s(t.$t("duration")))])]), t._v(" "), t._l(t.trajectoryList, (function(o, r) {
                return e("div", {
                    key: o.name
                }, [e("div", {
                    staticClass: "block-title",
                    class: {
                        "selected-block": o.selected
                    },
                    on: {
                        click: () => t.onSelectionItem(o, r),
                        dblclick: e => t.openRenameTrajectory(e, o, r)
                    }
                }, [t.enableSelection ? e("div", {
                    staticClass: "operate flex align-center"
                }, [e("i", {
                    staticClass: "iconfont",
                    class: o.selected ? "icon-circular-selected icon-selected" : "icon-circular-unselected"
                })]) : t._e(), t._v(" "), e("div", {
                    staticClass: "id"
                }, [t._v(t._s(r + 1))]), t._v(" "), e("input", {
                    directives: [{
                        name: "model",
                        rawName: "v-model",
                        value: t.curInputData.name,
                        expression: "curInputData.name"
                    }, {
                        name: "show",
                        rawName: "v-show",
                        value: t.editId === o.name,
                        expression: "editId === item.name"
                    }],
                    staticClass: "app-title-text input-focus",
                    attrs: {
                        type: "text"
                    },
                    domProps: {
                        value: t.curInputData.name
                    },
                    on: {
                        keydown: () => t.onRenameEnter(o),
                        blur: () => t.onRenameBlur(o),
                        focus: function(t) {
                            return t.preventDefault()
                        },
                        input: function(e) {
                            e.target.composing || t.$set(t.curInputData, "name", e.target.value)
                        }
                    }
                }), t._v(" "), e("div", {
                    directives: [{
                        name: "show",
                        rawName: "v-show",
                        value: t.editId !== o.name,
                        expression: "editId !== item.name"
                    }],
                    staticClass: "name-title"
                }, [t._v(" " + t._s(o.name))]), t._v(" "), e("div", {
                    staticClass: "duration"
                }, [t._v(t._s(t.formatSeconds(o.count / 250)))])])])
            }
            ))], 2)]), t._v(" "), e("div", {
                staticClass: "uf-space justify-center",
                attrs: {
                    slot: "footer"
                },
                slot: "footer"
            }, [e("button", {
                staticClass: "uf-button__cancel large",
                on: {
                    click: t.closeDialog
                }
            }, [t._v("\n      " + t._s(t.$t("close")) + "\n    ")])])])
        }
        ), [], !1, null, "7e34e9af", null).exports
          , j = {
            name: "OperateTrajectoryDialog",
            props: ["visible"],
            i18n: {
                messages: {
                    en: {
                        cancel: "Cancel",
                        ok: "OK",
                        title: "Please enter a project name",
                        errCanNotEmpty: "The name cannot be empty",
                        errNameIsExist: "The name already exists",
                        errNameNotLegal: "The name can only contain letters, numbers, underscores and no more than 30 characters",
                        deletetitle: "Delete Tip",
                        deletebtn: "Delete",
                        deletetext: "Are you sure to delete this project?",
                        deleteSuccess: "Delete success！",
                        deleteFailed: "Delete failed!"
                    },
                    cn: {
                        cancel: "取消",
                        ok: "确定",
                        title: "请输入项目名称",
                        errCanNotEmpty: "名字不能为空",
                        errNameIsExist: "名字已存在",
                        errNameNotLegal: "名字只能包含字母，数字，下划线且不超过30个字符",
                        deletetitle: "删除提示",
                        deletebtn: "删除",
                        deletetext: "是否确认删除选中项目",
                        deleteSuccess: "删除成功！",
                        deleteFailed: "删除失败"
                    }
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    trajName: "",
                    errorTips: ""
                }
            },
            mounted: function() {
                var t = this;
                this.$nextTick((function() {
                    t.$refs.trajName && t.$refs.trajName.$el.querySelector("input").focus()
                }
                ))
            },
            methods: {
                closeDialog: function() {
                    var t = this;
                    this.$emit("update:visible", !1),
                    this.$nextTick((function() {
                        t.trajName = ""
                    }
                    ))
                },
                deleteProject: function() {
                    var t = this;
                    this.model.LocalTraj.deleteTraj((function(e) {
                        t.cancel(),
                        t.$message({
                            type: 0 === e.code ? "success" : "error",
                            message: 0 === e.code ? t.$t("deleteSuccess") : t.$t("deleteFailed")
                        })
                    }
                    ))
                },
                createTraj: function(t) {
                    "" !== t.trim() && "" === this.errorTips && (this.closeDialog(),
                    this.$emit("confirm", t))
                },
                checkName: function(t) {
                    if (t)
                        if (t.length > 30)
                            this.errorTips = this.$t("errNameNotLegal");
                        else {
                            if (/^[-_\w]+$/.test(t)) {
                                var e = !1;
                                this.$parent.trajectoryList.forEach((function(o) {
                                    o.name === t && (e = !0)
                                }
                                )),
                                this.errorTips = e ? this.$t("errNameIsExist") : ""
                            } else
                                this.errorTips = this.$t("errNameNotLegal")
                        }
                    else
                        this.errorTips = this.$t("errCanNotEmpty")
                }
            },
            watch: {
                trajName: function(t) {
                    this.visible && this.checkName(t)
                },
                visible: function(t) {
                    t && (this.trajName = "",
                    this.errorTips = "")
                }
            }
        }
          , x = Object(a.a)(j, (function() {
            var t = this
              , e = t._self._c;
            return e("el-dialog", {
                staticClass: "uf-dialog",
                attrs: {
                    visible: t.visible,
                    "show-close": !1,
                    "close-on-click-modal": !1,
                    center: "",
                    width: "28%"
                },
                on: {
                    "update:visible": function(e) {
                        t.visible = e
                    }
                }
            }, [e("div", {
                attrs: {
                    slot: "title"
                },
                slot: "title"
            }, [t._v(t._s(t.$t("title")))]), t._v(" "), e("div", {
                staticClass: "min-height"
            }, [e("div", [e("el-input", {
                ref: "trajName",
                staticClass: "uf-txt-center",
                attrs: {
                    placeholder: "Please enter a project name"
                },
                nativeOn: {
                    keyup: function(e) {
                        return !e.type.indexOf("key") && t._k(e.keyCode, "enter", 13, e.key, "Enter") ? null : t.createTraj(t.trajName)
                    }
                },
                model: {
                    value: t.trajName,
                    callback: function(e) {
                        t.trajName = e
                    },
                    expression: "trajName"
                }
            }), t._v(" "), e("div", {
                staticClass: "uf-error-msg mt-px-5"
            }, [t._v(t._s(t.errorTips) + "  ")])], 1)]), t._v(" "), e("div", {
                staticClass: "uf-space justify-center",
                attrs: {
                    slot: "footer"
                },
                slot: "footer"
            }, [e("button", {
                staticClass: "uf-button__cancel large mw-50",
                on: {
                    click: t.closeDialog
                }
            }, [t._v(t._s(t.$t("cancel")))]), t._v(" "), e("button", {
                staticClass: "uf-button large mw-50",
                attrs: {
                    disabled: "" === t.trajName || "" !== t.errorTips
                },
                on: {
                    click: function(e) {
                        return t.createTraj(t.trajName)
                    }
                }
            }, [t._v("\n      " + t._s(t.$t("ok")) + "\n    ")])])])
        }
        ), [], !1, null, "0a97490e", null).exports
          , S = o(375)
          , k = o.n(S)
          , P = o(987)
          , $ = o(988)
          , L = {
            name: "RecordOrbitDialog",
            props: ["onCancel", "visible"],
            emit: ["setSelected"],
            i18n: {
                messages: {
                    en: {
                        runningTip: "Project is running!",
                        transcribeTip: "Start recording",
                        cancel: "Discard",
                        confirm: "Save",
                        incompatible: "Currently it is a simulated robotic arm and cannot perform track recording",
                        transcribeMethod: 'After clicking "Record", drag the end of the robotic arm to record the trajectory.',
                        transcribeMethodList6: 'Click "Record", press and hold the button at the end of the robotic arm and drag the robotic arm to record the trajectory.',
                        tipCancel: "Cancel",
                        tipConfirm: "Record",
                        transcribeOver: "Trajectory recording completed",
                        endRecording: "End Recording",
                        moveTips: "Move the robotic arm to record trajectory"
                    },
                    cn: {
                        runningTip: "项目正在运行!",
                        transcribeTip: "开始录制",
                        cancel: "放弃",
                        confirm: "保存",
                        incompatible: "当前为仿真机械臂，无法进行轨迹录制。",
                        transcribeMethod: "点击“录制”后，拖动机械臂末端录制轨迹",
                        transcribeMethodList6: "点击“录制”，按住机械臂末端按钮并拖动 机械臂来轨迹录制。",
                        tipCancel: "取消",
                        tipConfirm: "录制",
                        transcribeOver: "轨迹录制完成",
                        endRecording: "结束录制",
                        moveTips: "拖动机械臂开始录制轨迹"
                    }
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    gifSrc: $,
                    isRecord: !1,
                    stopRecording: !1,
                    duration: "00:00",
                    time: {
                        timer: null,
                        seconds: 0
                    },
                    step: "tips",
                    moveRecord: 0
                }
            },
            mounted: function() {},
            methods: {
                closeDialog: function() {
                    this.$emit("update:visible", !1)
                },
                switchTranscribe: function() {
                    if (console.log("this.isRecord==>", this.isRecord),
                    !this.isRecord)
                        return this.step = "checkMove",
                        this.moveRecord = 0,
                        void this.startRecordTraj();
                    this.gifSrc = $,
                    this.stopRecordTraj()
                },
                startRecordTraj: function() {
                    this.urgentStopRecord && 4 === this.xarmState || (this.model.commonStatusMgr.UrgentStopRecord = !1),
                    this.$parent.curTrajectoryItem.name ? this.recordTraj() : this.$parent.showOperateTrajectory = !0
                },
                recordTraj: function() {
                    var t = this;
                    this.model.commonStatusMgr.isShowRecordTip = !1;
                    var e = this.ft_sensor.use_in_control ? "startRecordTrajOpenFt" : "startRecordTraj";
                    window.CommandsTrajSocket[e](this.$parent.curTrajectoryItem.name, (function(e) {
                        -99 === e.code && (t.deleteTranscribe(),
                        t.$message({
                            message: t.$t("incompatible"),
                            type: "warning",
                            duration: 3e3,
                            showClose: !0
                        })),
                        t.isLite6 && e.data ? (e.data.count || 0 === e.data.count) && (t.$parent.curTrajectoryItem.points = e.data.points,
                        t.$parent.curTrajectoryItem.count = e.data.count,
                        t.stopRecording = !0,
                        t.time.timer && (clearInterval(t.time.timer),
                        t.time.timer = null),
                        t.switchMode(0),
                        t.isRecord = !1,
                        t.stopRecording = !1,
                        t.gifSrc = $) : console.log("Traj -> start 22", e)
                    }
                    ), this.ft_sensor.axis)
                },
                formatSeconds: function(t) {
                    var e = parseInt(t / 3600)
                      , o = parseInt((t - 3600 * e) / 60)
                      , r = parseInt((t - 3600 * e) % 60)
                      , n = "";
                    return e > 0 && (n = e < 10 ? "0".concat(e, ":") : "".concat(e, ":")),
                    n += o < 10 ? "0".concat(o, ":") : "".concat(o, ":"),
                    n += r < 10 ? "0".concat(r) : "".concat(r)
                },
                startTimer: function() {
                    this.time.seconds += 1,
                    this.duration = this.formatSeconds(this.time.seconds)
                },
                stopRecordTraj: function() {
                    var t = this;
                    console.log("stopRecordTraj==>", this.stopRecording),
                    this.stopRecording || (this.stopRecording = !0,
                    this.time.timer && (clearInterval(this.time.timer),
                    this.time.timer = null),
                    this.step = "save",
                    window.CommandsTrajSocket.pauseRecordTraj((function(e) {
                        window.CommandsTrajSocket.closeRecordFtSensor(),
                        0 === e.code && (t.duration = t.formatSeconds(e.data.record_seconds)),
                        console.log("pause over --------\x3e", e)
                    }
                    )))
                },
                switchMode: function(t) {
                    window.CommandsRobotSocket.switch_mode(t ? 2 : 0, (function(t) {}
                    ))
                },
                confirmSave: function() {
                    var t = this
                      , e = this.isLite6 ? "stopRecordTrajLite6" : "stopRecordTraj";
                    window.CommandsTrajSocket[e](this.$parent.curTrajectoryItem.name, (function(e) {
                        if (0 === e.code) {
                            var o = {
                                name: t.$parent.curTrajectoryItem.name,
                                points: e.data.points,
                                count: e.data.count / 250
                            };
                            t.$parent.curTrajectoryItem = o,
                            t.$parent.trajectoryList.forEach((function(e, r) {
                                e.name === t.$parent.curTrajectoryItem.name && t.$set(t.$parent.trajectoryList, r, o)
                            }
                            )),
                            t.switchMode(0),
                            t.cancelSave(),
                            t.$emit("refresh")
                        }
                    }
                    ))
                },
                cancelSave: function() {
                    this.isRecord = !1,
                    this.stopRecording = !1,
                    this.duration = "00:00",
                    this.closeDialog(),
                    this.step = "tips"
                },
                deleteTranscribe: function() {
                    var t = this;
                    sessionStorage.getItem("TrajName_") || this.$parent.curTrajectoryItem.name ? window.CommandsTrajSocket.pauseRecordTraj((function(e) {
                        window.CommandsTrajSocket.closeRecordFtSensor();
                        var o = "checkMove" === t.step && t.isLite6 ? "deleteLite6Traj" : "deleteTraj";
                        console.log("nameKey", o),
                        t.$parent.deleteTranscribe([t.$parent.curTrajectoryItem.name], (function() {
                            t.cancelSave()
                        }
                        ))
                    }
                    )) : this.cancelSave()
                },
                contrastJointRange: function(t, e) {
                    for (var o = 0; o < t.length; o++) {
                        var r = t[o]
                          , n = e[o];
                        if (Math.abs(r - n) > .5)
                            return console.log("大于0.5运动了===-------------==》"),
                            !0
                    }
                    return !1
                }
            },
            components: {},
            computed: {
                ft_sensor: function() {
                    return this.model.robot.state.info.ft_sensor
                },
                tcpLoad: function() {
                    return window.GlobalUtil.model.robot.state.tcpConfig.tcpLoad
                },
                currentTcpLoad: function() {
                    return this.tcpLoad && this.tcpLoad.currentConfig ? this.tcpLoad.currentConfig : [0, 0, 0, 0]
                },
                isLite6: function() {
                    return !(6 !== this.model.robot.state.info.xarm_axis || 9 !== this.model.robot.state.info.xarm_device_type)
                },
                isNewRecord: function() {
                    var t = this.$parent.curTrajectoryItem.count;
                    return (void 0 === t ? 0 : t) < 250
                },
                transcribeMethod: function() {
                    return this.isLite6 ? this.$t("transcribeMethodList6") : this.$t("transcribeMethod")
                },
                roboticArmMove: function() {},
                joints: function() {
                    return this.model.robot.state.local.joints
                },
                isCode37: function() {
                    return !(!this.model.exceptionModel.errorObj.controller || 37 !== this.model.exceptionModel.errorObj.controller.code)
                }
            },
            watch: {
                "model.exceptionModel.errorObj.controller": {
                    immediate: !0,
                    deep: !0,
                    handler: function(t) {
                        t && 37 == +t.code && this.deleteTranscribe()
                    }
                },
                joints: {
                    deep: !0,
                    handler: k()((function(t, e) {
                        "checkMove" === this.step && (this.contrastJointRange(t, e) && (this.moveRecord += 1))
                    }
                    ), 200)
                },
                moveRecord: function(t) {
                    t >= 3 && "checkMove" === this.step && (this.step = "transcribe",
                    this.gifSrc = P,
                    this.isRecord = !0,
                    this.time.seconds = 0,
                    this.duration = "00:00",
                    this.moveRecord = 0,
                    this.time.timer = setInterval(this.startTimer, 1e3))
                }
            }
        }
          , O = Object(a.a)(L, (function() {
            var t = this
              , e = t._self._c;
            return e("el-dialog", {
                staticClass: "uf-dialog",
                attrs: {
                    visible: t.visible,
                    "show-close": !1,
                    "close-on-click-modal": !1,
                    center: "",
                    width: "28%"
                },
                on: {
                    "update:visible": function(e) {
                        t.visible = e
                    }
                }
            }, [e("div", {
                staticClass: "rhythm-box min-height"
            }, ["tips" === t.step ? e("div", {
                staticClass: "use-method"
            }, [t._v(t._s(t.transcribeMethod))]) : ["transcribe", "checkMove"].includes(t.step) ? [["checkMove"].includes(t.step) ? e("div", {
                staticClass: "use-method"
            }, [t._v(t._s(t.$t("moveTips")))]) : t._e(), t._v(" "), e("div", {
                staticClass: "rhythm"
            }, [e("img", {
                ref: "gif",
                attrs: {
                    src: t.gifSrc
                }
            })]), t._v(" "), e("div", {
                staticClass: "time"
            }, [t._v(t._s(t.duration))])] : [e("div", {
                staticClass: "use-method",
                staticStyle: {
                    "font-size": "16px"
                }
            }, [t._v(t._s(t.$t("transcribeOver")))]), t._v(" "), e("div", {
                staticClass: "time"
            }, [t._v(t._s(t.duration))])]], 2), t._v(" "), e("div", {
                staticClass: "btn-area uf-space justify-center",
                attrs: {
                    slot: "footer"
                },
                slot: "footer"
            }, ["tips" === t.step ? [e("div", {
                staticClass: "uf-button__cancel large",
                on: {
                    click: t.deleteTranscribe
                }
            }, [t._v(t._s(t.$t("tipCancel")))]), t._v(" "), e("div", {
                staticClass: "uf-button large",
                on: {
                    click: t.switchTranscribe
                }
            }, [t._v(t._s(t.$t("tipConfirm")))])] : ["checkMove"].includes(t.step) ? e("button", {
                staticClass: "uf-button__cancel transcribe-btn",
                on: {
                    click: t.deleteTranscribe
                }
            }, [t._v("\n      " + t._s(t.$t("tipCancel")) + "\n    ")]) : ["transcribe"].includes(t.step) ? e("div", {
                staticClass: "uf-button transcribe-btn",
                on: {
                    click: t.switchTranscribe
                }
            }, [t._v("\n      " + t._s(t.$t("endRecording")) + "\n      "), t.isRecord ? t._e() : e("div", {
                staticClass: "tips"
            }, [t._v(t._s(t.$t("transcribeTip")))])]) : [e("div", {
                staticClass: "uf-button__cancel transcribe-cancel",
                on: {
                    click: t.deleteTranscribe
                }
            }, [t._v(t._s(t.$t("cancel")))]), t._v(" "), e("div", {
                staticClass: "uf-button transcribe-confirm",
                on: {
                    click: t.confirmSave
                }
            }, [t._v(t._s(t.$t("confirm")))])]], 2)])
        }
        ), [], !1, null, "210c9659", null).exports;
        function E(t) {
            return E = "function" == typeof Symbol && "symbol" == typeof Symbol.iterator ? function(t) {
                return typeof t
            }
            : function(t) {
                return t && "function" == typeof Symbol && t.constructor === Symbol && t !== Symbol.prototype ? "symbol" : typeof t
            }
            ,
            E(t)
        }
        function R() {
            R = function() {
                return e
            }
            ;
            var t, e = {}, o = Object.prototype, r = o.hasOwnProperty, n = Object.defineProperty || function(t, e, o) {
                t[e] = o.value
            }
            , i = "function" == typeof Symbol ? Symbol : {}, a = i.iterator || "@@iterator", s = i.asyncIterator || "@@asyncIterator", c = i.toStringTag || "@@toStringTag";
            function l(t, e, o) {
                return Object.defineProperty(t, e, {
                    value: o,
                    enumerable: !0,
                    configurable: !0,
                    writable: !0
                }),
                t[e]
            }
            try {
                l({}, "")
            } catch (t) {
                l = function(t, e, o) {
                    return t[e] = o
                }
            }
            function u(t, e, o, r) {
                var i = e && e.prototype instanceof b ? e : b
                  , a = Object.create(i.prototype)
                  , s = new L(r || []);
                return n(a, "_invoke", {
                    value: S(t, o, s)
                }),
                a
            }
            function d(t, e, o) {
                try {
                    return {
                        type: "normal",
                        arg: t.call(e, o)
                    }
                } catch (t) {
                    return {
                        type: "throw",
                        arg: t
                    }
                }
            }
            e.wrap = u;
            var p = "suspendedStart"
              , m = "suspendedYield"
              , f = "executing"
              , h = "completed"
              , v = {};
            function b() {}
            function g() {}
            function y() {}
            var _ = {};
            l(_, a, (function() {
                return this
            }
            ));
            var w = Object.getPrototypeOf
              , C = w && w(w(O([])));
            C && C !== o && r.call(C, a) && (_ = C);
            var T = y.prototype = b.prototype = Object.create(_);
            function j(t) {
                ["next", "throw", "return"].forEach((function(e) {
                    l(t, e, (function(t) {
                        return this._invoke(e, t)
                    }
                    ))
                }
                ))
            }
            function x(t, e) {
                function o(n, i, a, s) {
                    var c = d(t[n], t, i);
                    if ("throw" !== c.type) {
                        var l = c.arg
                          , u = l.value;
                        return u && "object" == E(u) && r.call(u, "__await") ? e.resolve(u.__await).then((function(t) {
                            o("next", t, a, s)
                        }
                        ), (function(t) {
                            o("throw", t, a, s)
                        }
                        )) : e.resolve(u).then((function(t) {
                            l.value = t,
                            a(l)
                        }
                        ), (function(t) {
                            return o("throw", t, a, s)
                        }
                        ))
                    }
                    s(c.arg)
                }
                var i;
                n(this, "_invoke", {
                    value: function(t, r) {
                        function n() {
                            return new e((function(e, n) {
                                o(t, r, e, n)
                            }
                            ))
                        }
                        return i = i ? i.then(n, n) : n()
                    }
                })
            }
            function S(e, o, r) {
                var n = p;
                return function(i, a) {
                    if (n === f)
                        throw Error("Generator is already running");
                    if (n === h) {
                        if ("throw" === i)
                            throw a;
                        return {
                            value: t,
                            done: !0
                        }
                    }
                    for (r.method = i,
                    r.arg = a; ; ) {
                        var s = r.delegate;
                        if (s) {
                            var c = k(s, r);
                            if (c) {
                                if (c === v)
                                    continue;
                                return c
                            }
                        }
                        if ("next" === r.method)
                            r.sent = r._sent = r.arg;
                        else if ("throw" === r.method) {
                            if (n === p)
                                throw n = h,
                                r.arg;
                            r.dispatchException(r.arg)
                        } else
                            "return" === r.method && r.abrupt("return", r.arg);
                        n = f;
                        var l = d(e, o, r);
                        if ("normal" === l.type) {
                            if (n = r.done ? h : m,
                            l.arg === v)
                                continue;
                            return {
                                value: l.arg,
                                done: r.done
                            }
                        }
                        "throw" === l.type && (n = h,
                        r.method = "throw",
                        r.arg = l.arg)
                    }
                }
            }
            function k(e, o) {
                var r = o.method
                  , n = e.iterator[r];
                if (n === t)
                    return o.delegate = null,
                    "throw" === r && e.iterator.return && (o.method = "return",
                    o.arg = t,
                    k(e, o),
                    "throw" === o.method) || "return" !== r && (o.method = "throw",
                    o.arg = new TypeError("The iterator does not provide a '" + r + "' method")),
                    v;
                var i = d(n, e.iterator, o.arg);
                if ("throw" === i.type)
                    return o.method = "throw",
                    o.arg = i.arg,
                    o.delegate = null,
                    v;
                var a = i.arg;
                return a ? a.done ? (o[e.resultName] = a.value,
                o.next = e.nextLoc,
                "return" !== o.method && (o.method = "next",
                o.arg = t),
                o.delegate = null,
                v) : a : (o.method = "throw",
                o.arg = new TypeError("iterator result is not an object"),
                o.delegate = null,
                v)
            }
            function P(t) {
                var e = {
                    tryLoc: t[0]
                };
                1 in t && (e.catchLoc = t[1]),
                2 in t && (e.finallyLoc = t[2],
                e.afterLoc = t[3]),
                this.tryEntries.push(e)
            }
            function $(t) {
                var e = t.completion || {};
                e.type = "normal",
                delete e.arg,
                t.completion = e
            }
            function L(t) {
                this.tryEntries = [{
                    tryLoc: "root"
                }],
                t.forEach(P, this),
                this.reset(!0)
            }
            function O(e) {
                if (e || "" === e) {
                    var o = e[a];
                    if (o)
                        return o.call(e);
                    if ("function" == typeof e.next)
                        return e;
                    if (!isNaN(e.length)) {
                        var n = -1
                          , i = function o() {
                            for (; ++n < e.length; )
                                if (r.call(e, n))
                                    return o.value = e[n],
                                    o.done = !1,
                                    o;
                            return o.value = t,
                            o.done = !0,
                            o
                        };
                        return i.next = i
                    }
                }
                throw new TypeError(E(e) + " is not iterable")
            }
            return g.prototype = y,
            n(T, "constructor", {
                value: y,
                configurable: !0
            }),
            n(y, "constructor", {
                value: g,
                configurable: !0
            }),
            g.displayName = l(y, c, "GeneratorFunction"),
            e.isGeneratorFunction = function(t) {
                var e = "function" == typeof t && t.constructor;
                return !!e && (e === g || "GeneratorFunction" === (e.displayName || e.name))
            }
            ,
            e.mark = function(t) {
                return Object.setPrototypeOf ? Object.setPrototypeOf(t, y) : (t.__proto__ = y,
                l(t, c, "GeneratorFunction")),
                t.prototype = Object.create(T),
                t
            }
            ,
            e.awrap = function(t) {
                return {
                    __await: t
                }
            }
            ,
            j(x.prototype),
            l(x.prototype, s, (function() {
                return this
            }
            )),
            e.AsyncIterator = x,
            e.async = function(t, o, r, n, i) {
                void 0 === i && (i = Promise);
                var a = new x(u(t, o, r, n),i);
                return e.isGeneratorFunction(o) ? a : a.next().then((function(t) {
                    return t.done ? t.value : a.next()
                }
                ))
            }
            ,
            j(T),
            l(T, c, "Generator"),
            l(T, a, (function() {
                return this
            }
            )),
            l(T, "toString", (function() {
                return "[object Generator]"
            }
            )),
            e.keys = function(t) {
                var e = Object(t)
                  , o = [];
                for (var r in e)
                    o.push(r);
                return o.reverse(),
                function t() {
                    for (; o.length; ) {
                        var r = o.pop();
                        if (r in e)
                            return t.value = r,
                            t.done = !1,
                            t
                    }
                    return t.done = !0,
                    t
                }
            }
            ,
            e.values = O,
            L.prototype = {
                constructor: L,
                reset: function(e) {
                    if (this.prev = 0,
                    this.next = 0,
                    this.sent = this._sent = t,
                    this.done = !1,
                    this.delegate = null,
                    this.method = "next",
                    this.arg = t,
                    this.tryEntries.forEach($),
                    !e)
                        for (var o in this)
                            "t" === o.charAt(0) && r.call(this, o) && !isNaN(+o.slice(1)) && (this[o] = t)
                },
                stop: function() {
                    this.done = !0;
                    var t = this.tryEntries[0].completion;
                    if ("throw" === t.type)
                        throw t.arg;
                    return this.rval
                },
                dispatchException: function(e) {
                    if (this.done)
                        throw e;
                    var o = this;
                    function n(r, n) {
                        return s.type = "throw",
                        s.arg = e,
                        o.next = r,
                        n && (o.method = "next",
                        o.arg = t),
                        !!n
                    }
                    for (var i = this.tryEntries.length - 1; i >= 0; --i) {
                        var a = this.tryEntries[i]
                          , s = a.completion;
                        if ("root" === a.tryLoc)
                            return n("end");
                        if (a.tryLoc <= this.prev) {
                            var c = r.call(a, "catchLoc")
                              , l = r.call(a, "finallyLoc");
                            if (c && l) {
                                if (this.prev < a.catchLoc)
                                    return n(a.catchLoc, !0);
                                if (this.prev < a.finallyLoc)
                                    return n(a.finallyLoc)
                            } else if (c) {
                                if (this.prev < a.catchLoc)
                                    return n(a.catchLoc, !0)
                            } else {
                                if (!l)
                                    throw Error("try statement without catch or finally");
                                if (this.prev < a.finallyLoc)
                                    return n(a.finallyLoc)
                            }
                        }
                    }
                },
                abrupt: function(t, e) {
                    for (var o = this.tryEntries.length - 1; o >= 0; --o) {
                        var n = this.tryEntries[o];
                        if (n.tryLoc <= this.prev && r.call(n, "finallyLoc") && this.prev < n.finallyLoc) {
                            var i = n;
                            break
                        }
                    }
                    i && ("break" === t || "continue" === t) && i.tryLoc <= e && e <= i.finallyLoc && (i = null);
                    var a = i ? i.completion : {};
                    return a.type = t,
                    a.arg = e,
                    i ? (this.method = "next",
                    this.next = i.finallyLoc,
                    v) : this.complete(a)
                },
                complete: function(t, e) {
                    if ("throw" === t.type)
                        throw t.arg;
                    return "break" === t.type || "continue" === t.type ? this.next = t.arg : "return" === t.type ? (this.rval = this.arg = t.arg,
                    this.method = "return",
                    this.next = "end") : "normal" === t.type && e && (this.next = e),
                    v
                },
                finish: function(t) {
                    for (var e = this.tryEntries.length - 1; e >= 0; --e) {
                        var o = this.tryEntries[e];
                        if (o.finallyLoc === t)
                            return this.complete(o.completion, o.afterLoc),
                            $(o),
                            v
                    }
                },
                catch: function(t) {
                    for (var e = this.tryEntries.length - 1; e >= 0; --e) {
                        var o = this.tryEntries[e];
                        if (o.tryLoc === t) {
                            var r = o.completion;
                            if ("throw" === r.type) {
                                var n = r.arg;
                                $(o)
                            }
                            return n
                        }
                    }
                    throw Error("illegal catch attempt")
                },
                delegateYield: function(e, o, r) {
                    return this.delegate = {
                        iterator: O(e),
                        resultName: o,
                        nextLoc: r
                    },
                    "next" === this.method && (this.arg = t),
                    v
                }
            },
            e
        }
        function I(t, e, o, r, n, i, a) {
            try {
                var s = t[i](a)
                  , c = s.value
            } catch (t) {
                return void o(t)
            }
            s.done ? e(c) : Promise.resolve(c).then(r, n)
        }
        function F(t) {
            return function() {
                var e = this
                  , o = arguments;
                return new Promise((function(r, n) {
                    var i = t.apply(e, o);
                    function a(t) {
                        I(i, r, n, a, s, "next", t)
                    }
                    function s(t) {
                        I(i, r, n, a, s, "throw", t)
                    }
                    a(void 0)
                }
                ))
            }
        }
        function D(t, e) {
            var o = Object.keys(t);
            if (Object.getOwnPropertySymbols) {
                var r = Object.getOwnPropertySymbols(t);
                e && (r = r.filter((function(e) {
                    return Object.getOwnPropertyDescriptor(t, e).enumerable
                }
                ))),
                o.push.apply(o, r)
            }
            return o
        }
        function A(t) {
            for (var e = 1; e < arguments.length; e++) {
                var o = null != arguments[e] ? arguments[e] : {};
                e % 2 ? D(Object(o), !0).forEach((function(e) {
                    M(t, e, o[e])
                }
                )) : Object.getOwnPropertyDescriptors ? Object.defineProperties(t, Object.getOwnPropertyDescriptors(o)) : D(Object(o)).forEach((function(e) {
                    Object.defineProperty(t, e, Object.getOwnPropertyDescriptor(o, e))
                }
                ))
            }
            return t
        }
        function M(t, e, o) {
            var r;
            return r = function(t, e) {
                if ("object" != E(t) || !t)
                    return t;
                var o = t[Symbol.toPrimitive];
                if (void 0 !== o) {
                    var r = o.call(t, e || "default");
                    if ("object" != E(r))
                        return r;
                    throw new TypeError("@@toPrimitive must return a primitive value.")
                }
                return ("string" === e ? String : Number)(t)
            }(e, "string"),
            (e = "symbol" == E(r) ? r : r + "")in t ? Object.defineProperty(t, e, {
                value: o,
                enumerable: !0,
                configurable: !0,
                writable: !0
            }) : t[e] = o,
            t
        }
        var G = {
            name: "TrackRecording",
            i18n: {
                messages: {
                    en: {
                        Trajectory: "Recording",
                        runningTip: "Project is running!",
                        recordingTip: "Project is recording!",
                        deleteTip: "Are you sure to delete -- ",
                        deleteSuccess: "Delete success！",
                        deleteFailed: "Delete failed!",
                        tip: "Tip",
                        manual: "MANUAL MODE",
                        yes: "Yes",
                        no: "No",
                        startRecording: "Start Recording",
                        stopRecording: "Stop Recording",
                        stopRecordingLite6: "Recording, release the manual mode button to end recording",
                        runTrajectory: "Run Trajectory",
                        stopRunning: "Stop Running",
                        myProject: "My Project",
                        new: "New",
                        importText: "Import",
                        selectText: "Select",
                        unselectText: "Cancel",
                        uploadSuccess: "Upload success",
                        uploadFailed: "Upload failed, content error",
                        uploadUnCompatible: "Engineering is not compatible",
                        uploadFmtLimit: "Only supports .gz or .xml format",
                        uploadSizeLimit: "Upload size can not over 10M",
                        defaultPageText: "You don't have a project yet",
                        createNow: "Create now",
                        downloadSuccess: "Download success",
                        downloadFailed: "Download failed",
                        selectAll: "Select All",
                        times: "Times",
                        speed: "Speed",
                        deselectAll: "Deselect All",
                        export: "Export",
                        delete: "Delete",
                        limitVersionTip: "The current firmware version does not support trajectory recording, please upgrade to V2.0.0 or later.",
                        btnCancel: "Close",
                        controlUseManualHoverTip: "Choose at least one manual mode direction please.",
                        placeholder: "Please create a new file first",
                        noFrictionData: "No friction data, please contact technical support.",
                        continue: "Continue",
                        pause: "Pause",
                        stop: "Stop",
                        play: "Play"
                    },
                    cn: {
                        Trajectory: "轨迹录制",
                        runningTip: "项目正在运行!",
                        recordingTip: "项目正在录制!",
                        deleteTip: "是否删除该文件 -- ",
                        deleteSuccess: "删除成功！",
                        deleteFailed: "删除失败",
                        tip: "提示",
                        manual: "手动模式",
                        yes: "是",
                        no: "否",
                        startRecording: "开始录制",
                        stopRecording: "结束录制",
                        stopRecordingLite6: "录制中，松开手动模式按钮结束录制",
                        runTrajectory: "运行轨迹",
                        stopRunning: "停止运行",
                        myProject: "我的项目",
                        new: "新建",
                        importText: "导入",
                        selectText: "选择",
                        unselectText: "取消",
                        uploadSuccess: "上传成功",
                        uploadFailed: "上传失败, 内容不对",
                        uploadUnCompatible: "上传的工程与机械臂不兼容",
                        uploadFmtLimit: "只支持.gz或.xml格式",
                        uploadSizeLimit: "上传大小不能超过10M",
                        defaultPageText: "您还没有项目～",
                        createNow: "立即新建",
                        downloadSuccess: "下载成功",
                        downloadFailed: "下载失败",
                        selectAll: "全选",
                        times: "循环",
                        speed: "倍速",
                        deselectAll: "取消全选",
                        export: "导出",
                        delete: "删除",
                        limitVersionTip: "当前固件版本不支持轨迹录制，请升级到V2.0.0或以上版本",
                        btnCancel: "关闭",
                        controlUseManualHoverTip: "请至少选择一个方向激活手动模式",
                        placeholder: "请先新建文件",
                        continue: "继续",
                        pause: "暂停",
                        stop: "停止",
                        play: "播放",
                        noFrictionData: "无摩擦力数据，请联系技术支持"
                    }
                }
            },
            components: {
                UFEmpty: o(402).a,
                RecordOrbitDialog: O,
                OperateTrajectoryDialog: x,
                TrajectoryList: T
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    showTrajectoryList: !1,
                    trajectoryList: [],
                    curTrajectory: "",
                    curTrajectoryItem: {},
                    duration: "00:00",
                    speed: 1,
                    times: 1,
                    isPlayback: !1,
                    pointIndex: 0,
                    playbackTimes: 0,
                    pointInterval: 0,
                    runTime: 0,
                    showOperateTrajectory: !1,
                    showRecordOrbit: !1,
                    selectRecord: "",
                    playProgressValue: 0,
                    playTimer: 0,
                    pauseTime: 0,
                    pauseState: !1
                }
            },
            methods: {
                getTrajectoryList: function() {
                    var t = this;
                    window.CommandsTrajSocket.listTrajs((function(e) {
                        0 === e.code && (t.trajectoryList = e.data.map((function(t) {
                            return A(A({}, t), {}, {
                                selected: !1
                            })
                        }
                        )),
                        !t.curTrajectory && e.data.length && (t.curTrajectory = e.data[0].name))
                    }
                    ))
                },
                runTrackState: function() {
                    var t = this;
                    return F(R().mark((function e() {
                        var o, r;
                        return R().wrap((function(e) {
                            for (; ; )
                                switch (e.prev = e.next) {
                                case 0:
                                    return o = null,
                                    r = 0,
                                    e.abrupt("return", new Promise((function(e, n) {
                                        o = setInterval((function() {
                                            ++r > 1e3 && (clearInterval(o),
                                            e()),
                                            t.isRunTrackState && (clearInterval(o),
                                            e())
                                        }
                                        ), 200)
                                    }
                                    )));
                                case 3:
                                case "end":
                                    return e.stop()
                                }
                        }
                        ), e)
                    }
                    )))()
                },
                playbackTraj: function() {
                    var t = this;
                    return F(R().mark((function e() {
                        return R().wrap((function(e) {
                            for (; ; )
                                switch (e.prev = e.next) {
                                case 0:
                                    if (t.curTrajectory) {
                                        e.next = 2;
                                        break
                                    }
                                    return e.abrupt("return");
                                case 2:
                                    if (!(t.curTrajectoryItem.count <= 0)) {
                                        e.next = 4;
                                        break
                                    }
                                    return e.abrupt("return");
                                case 4:
                                    if (window.GlobalUtil.model.appState.checkProtectionStoppedState = !0,
                                    !t.$store.getters.isProtectionStopped) {
                                        e.next = 7;
                                        break
                                    }
                                    return e.abrupt("return");
                                case 7:
                                    if (!t.$store.getters.armNotEnabled) {
                                        e.next = 9;
                                        break
                                    }
                                    return e.abrupt("return", t.model.exceptionModel.showErrorDialogFunc());
                                case 9:
                                    if (!t.isPlayback) {
                                        e.next = 12;
                                        break
                                    }
                                    return t.stopPlayback(),
                                    e.abrupt("return");
                                case 12:
                                    return t.isPlayback = !0,
                                    t.clearPlayProgress(!0),
                                    t.stateOnline ? window.CommandsTrajSocket.playbackTraj(t.curTrajectory, t.times, t.speed, (function(e) {
                                        t.isPlayback = !1;
                                        var o = t.model.LocalTraj.curTraj.name;
                                        t.model.commonStatusMgr.warningFlag = !1;
                                        var r = "";
                                        1 === e.code ? r = v.k ? "加载轨迹".concat(o, "失败") : "Load trajectory ".concat(o, " failed") : 2 === e.code ? r = v.k ? "加载轨迹".concat(o, "失败(通信错误)") : "Load trajectory ".concat(o, " failed(communicate fialed)") : 3 === e.code ? r = v.k ? "加载轨迹".concat(o, "失败(超时)") : "Load trajectory ".concat(o, " failed(timeout)") : 4 === e.code ? r = v.k ? "回放轨迹".concat(o, "失败") : "Playback trajectory ".concat(o, " failed") : 5 === e.code ? r = v.k ? "回放轨迹".concat(o, "失败(超时)") : "Playback trajectory ".concat(o, " failed(timeout)") : 9 === e.code && (r = null),
                                        r && (clearInterval(t.pointInterval),
                                        t.model.commonStatusMgr.warningTitle = v.k ? "警告" : "Warning",
                                        t.model.commonStatusMgr.warningDesc = "",
                                        t.model.commonStatusMgr.warningFlag = !0,
                                        t.model.commonStatusMgr.warningText = r)
                                    }
                                    )) : t.curTrajectoryItem.points && (t.pointIndex = 0,
                                    t.playbackTimes = 0,
                                    t.pointInterval = setInterval((function() {
                                        if (t.pointIndex >= t.curTrajectoryItem.points.length)
                                            return clearInterval(b.a.pointInterval),
                                            t.pointInterval = null,
                                            void (t.isPlayback = !1);
                                        for (var e = t.curTrajectoryItem.points[t.pointIndex]; e.length < 7; )
                                            e.push(0);
                                        t.model.robot.state.local.joints = e,
                                        !t.pauseState && (t.pointIndex += 1)
                                    }
                                    ), 10)),
                                    e.next = 17,
                                    t.runTrackState();
                                case 17:
                                    t.setPlayProgressValue();
                                case 18:
                                case "end":
                                    return e.stop()
                                }
                        }
                        ), e)
                    }
                    )))()
                },
                stopPlayback: function() {
                    var t = this;
                    this.clearPlayProgress(!0),
                    this.pointInterval ? (clearInterval(this.pointInterval),
                    this.pointInterval = null) : window.CommandsRobotSocket.urgentStop({}, (function() {
                        t.pauseState = !1
                    }
                    )),
                    this.isPlayback = !1
                },
                checkTimes: function() {
                    var t = this;
                    this.$nextTick((function() {
                        t.times = Math.floor(t.times),
                        t.times < 1 || !t.times ? t.times = 1 : t.times > 999 && (t.times = 999)
                    }
                    ))
                },
                createTrajectory: function() {
                    var t = this
                      , e = arguments.length > 0 && void 0 !== arguments[0] ? arguments[0] : "";
                    return e ? (window.CommandsTrajSocket.createTraj(e, (function(o) {
                        if (0 === o.code) {
                            t.showRecordOrbit = !0,
                            t.selectRecord = e;
                            var r = {
                                name: e,
                                count: 0,
                                points: []
                            };
                            t.trajectoryList.push(r),
                            t.curTrajectoryItem = r,
                            t.curTrajectory = e
                        }
                    }
                    )),
                    !1) : (window.GlobalUtil.model.appState.checkProtectionStoppedState = !0,
                    this.$store.getters.armNotEnabled ? (this.model.exceptionModel.showErrorDialogFunc(),
                    !1) : !!this.manualModeParameters && (0 !== this.check() || (this.showOperateTrajectory = !0),
                    !1))
                },
                deleteTranscribe: function(t, e) {
                    var o = this
                      , r = function(t) {
                        0 === t.code && (o.curTrajectory = "",
                        o.curTrajectoryItem = {},
                        o.getTrajectoryList()),
                        e && e(t)
                    };
                    this.$store.getters.isLite6 && window.CommandsTrajSocket.deleteLite6Traj(t, r),
                    window.CommandsTrajSocket.deleteTraj(t, r)
                },
                check: function() {
                    return this.isPlayback ? (this.$message({
                        message: this.$t("runningTip"),
                        type: "warning",
                        duration: 1e3
                    }),
                    -1) : this.isRecord ? (this.$message({
                        message: this.$t("recordingTip"),
                        type: "warning",
                        duration: 1e3
                    }),
                    -1) : 0
                },
                clearPlayProgress: function() {
                    arguments.length > 0 && void 0 !== arguments[0] && arguments[0] && (this.runTime = 0),
                    this.playProgressValue = 0,
                    clearInterval(this.playTimer)
                },
                setPlayProgressValue: function() {
                    var t = this;
                    this.clearPlayProgress();
                    var e = Math.floor(this.curTrajectoryItem.count / this.speed * this.times)
                      , o = (new Date).getTime()
                      , r = 0;
                    requestAnimationFrame((function n() {
                        var i = (new Date).getTime();
                        if (t.isPlayback) {
                            if (t.pauseState)
                                return 0 === r && (r = i - o),
                                void requestAnimationFrame(n);
                            0 !== r && (o = i - r,
                            r = 0),
                            t.runTime = (i - o) / 1e3,
                            t.runTime >= e ? t.playProgressValue = 100 : (t.playProgressValue = t.runTime / e * 100,
                            requestAnimationFrame(n))
                        } else
                            t.clearPlayProgress()
                    }
                    ))
                },
                pauseRunTraj: function() {
                    var t = this;
                    this.isPlayback && window.CommandsRobotSocket.setState(this.pauseState ? 0 : 3, (function(e) {
                        0 === e.code && (t.pauseState = !t.pauseState)
                    }
                    ))
                }
            },
            computed: {
                xArmState: function() {
                    return this.model.robot.state.info.xarm_state
                },
                isConnected: function() {
                    return !!this.model.robot.state.info.socketConnected
                },
                stateOnline: function() {
                    return !0 === this.$store.state.robot.info.online
                },
                playProgress: function() {
                    var t = (this.curTrajectoryItem || {}).count
                      , e = void 0 === t ? 0 : t;
                    if (!e)
                        return {
                            total: "00:00",
                            cur: "00:00",
                            value: 0
                        };
                    var o = 1 / this.speed * this.times
                      , r = this.runTime;
                    return {
                        total: Object(v.f)(e * o),
                        cur: Object(v.f)(r),
                        value: this.runTime / Math.floor(e * o) * 100
                    }
                },
                ft_sensor: function() {
                    return this.model.robot.state.info.ft_sensor
                },
                manualModeParameters: function() {
                    var t = this.ft_sensor
                      , e = t.use_in_control
                      , o = t.axis;
                    return !(e && !o.includes(1))
                },
                notFrictionForce: function() {
                    return !this.model.commonStatusMgr.debugMode && !this.model.robot.state.info.robotSn
                },
                isRunTrackState: function() {
                    return 11 === this.model.robot.state.info.xarm_mode
                }
            },
            watch: {
                isConnected: {
                    immediate: !0,
                    handler: function(t) {
                        t && this.getTrajectoryList()
                    }
                },
                curTrajectory: function(t) {
                    var e = this;
                    t && (this.runTime = 0,
                    window.CommandsTrajSocket.loadTraj(t, (function(t) {
                        var o = e.curTrajectory;
                        if (0 === t.code) {
                            var r = t.data
                              , n = r.count
                              , i = r.points;
                            return e.curTrajectoryItem = {
                                points: i,
                                count: n / 250,
                                name: o
                            },
                            void (e.playProgressValue = 0)
                        }
                        e.curTrajectoryItem = {}
                    }
                    )))
                },
                xArmState: function(t) {
                    4 === t && this.isPlayback && this.stopPlayback()
                }
            }
        }
          , N = G
          , U = Object(a.a)(N, (function() {
            var t = this
              , e = t._self._c;
            return e("div", {
                staticClass: "track-recording"
            }, [e("div", {
                staticClass: "header"
            }, [e("div", {
                staticClass: "title font-vw-20"
            }, [t._v(t._s(t.$t("Trajectory")))]), t._v(" "), e("div", {
                staticClass: "operation"
            }, [e("button", {
                staticClass: "box",
                class: t.notFrictionForce ? "disabled" : "",
                attrs: {
                    disabled: t.notFrictionForce
                },
                on: {
                    click: () => t.createTrajectory()
                }
            }, [e("i", {
                staticClass: "iconfont icon-add font-vw-16"
            })]), t._v(" "), e("button", {
                staticClass: "box",
                class: t.notFrictionForce ? "disabled" : "",
                attrs: {
                    disabled: t.trajectoryList.length <= 0 || t.notFrictionForce
                },
                on: {
                    click: function(e) {
                        t.showTrajectoryList = !0
                    }
                }
            }, [e("i", {
                staticClass: "iconfont icon-more font-vw-16"
            })])])]), t._v(" "), t.trajectoryList.length && !t.notFrictionForce ? [e("div", {
                staticClass: "progress-body"
            }, [e("el-select", {
                staticClass: "uf-select indent trajectory-select font-vw-18",
                attrs: {
                    disabled: t.isPlayback,
                    placeholder: t.$t("placeholder")
                },
                model: {
                    value: t.curTrajectory,
                    callback: function(e) {
                        t.curTrajectory = e
                    },
                    expression: "curTrajectory"
                }
            }, t._l(t.trajectoryList, (function(t) {
                return e("el-option", {
                    key: t.name,
                    attrs: {
                        label: t.name,
                        value: t.name
                    }
                })
            }
            )), 1), t._v(" "), e("el-progress", {
                staticClass: "uf-progress small",
                attrs: {
                    percentage: t.playProgressValue,
                    "show-text": !1
                }
            }), t._v(" "), e("audio", {
                ref: "audioRef",
                staticStyle: {
                    display: "none"
                }
            }), t._v(" "), e("div", {
                staticClass: "time"
            }, [e("div", {
                staticClass: "start font-vw-12"
            }, [t._v(t._s(t.curTrajectory ? t.playProgress.cur : "00:00"))]), t._v(" "), e("div", {
                staticClass: "end font-vw-12"
            }, [t._v(t._s(t.curTrajectory ? t.playProgress.total : "00:00"))])])], 1), t._v(" "), e("div", {
                staticClass: "play-footer",
                class: t.trajectoryList.length <= 0 ? "disable" : ""
            }, [e("div", {
                staticClass: "play-btn",
                class: {
                    disabled: t.curTrajectoryItem.count <= 0,
                    active: t.isPlayback
                },
                on: {
                    click: t.playbackTraj
                }
            }, [e("i", {
                staticClass: "iconfont circular",
                class: t.isPlayback ? "icon-ide-stop" : "icon-ide-play"
            }), t._v(" "), e("div", {
                staticClass: "label font-vw-14"
            }, [t._v(t._s(t.isPlayback ? t.$t("stop") : t.$t("play")))])]), t._v(" "), e("div", {
                staticClass: "play-btn",
                class: {
                    disabled: !t.isPlayback,
                    active: t.pauseState
                },
                on: {
                    click: t.pauseRunTraj
                }
            }, [e("i", {
                staticClass: "iconfont circular",
                class: t.pauseState ? "icon-ide-continue" : "icon-ide-suspend2"
            }), t._v(" "), e("div", {
                staticClass: "label font-vw-14"
            }, [t._v(t._s(t.pauseState ? t.$t("continue") : t.$t("pause")))])]), t._v(" "), e("div", {
                staticClass: "play-btn"
            }, [e("el-select", {
                staticClass: "uf-select indent circular",
                attrs: {
                    disabled: t.isPlayback,
                    "popper-class": "speed-popper"
                },
                model: {
                    value: t.speed,
                    callback: function(e) {
                        t.speed = e
                    },
                    expression: "speed"
                }
            }, [e("el-option", {
                attrs: {
                    value: 4,
                    label: "x4"
                }
            }), t._v(" "), e("el-option", {
                attrs: {
                    value: 2,
                    label: "x2"
                }
            }), t._v(" "), e("el-option", {
                attrs: {
                    value: 1,
                    label: "x1"
                }
            })], 1), t._v(" "), e("div", {
                staticClass: "label font-vw-14"
            }, [t._v(t._s(t.$t("speed")))])], 1), t._v(" "), e("div", {
                staticClass: "play-btn"
            }, [e("el-input", {
                staticClass: "loop-input uf-input uf-txt-center uf-input unspin circular uf-font-medium",
                attrs: {
                    disabled: t.isPlayback,
                    maxlength: "3",
                    min: 1,
                    type: "number"
                },
                on: {
                    change: t.checkTimes
                },
                model: {
                    value: t.times,
                    callback: function(e) {
                        t.times = t._n(e)
                    },
                    expression: "times"
                }
            }), t._v(" "), e("div", {
                staticClass: "label font-vw-14"
            }, [t._v(t._s(t.$t("times")))])], 1)])] : e("UFEmpty", {
                staticClass: "hpx-200 position-center mt-px-30",
                attrs: {
                    description: t.notFrictionForce ? `<i class='el-icon-warning-outline font-vw-16'></i> ${t.$t("noFrictionData")}` : t.$t("defaultPageText"),
                    size: t.notFrictionForce ? "300" : "",
                    type: t.notFrictionForce ? "config" : "empty",
                    gap: t.notFrictionForce ? 30 : 0
                }
            }), t._v(" "), e("TrajectoryList", {
                attrs: {
                    visible: t.showTrajectoryList,
                    "trajectory-list": t.trajectoryList,
                    "cur-trajectory": t.curTrajectory
                },
                on: {
                    "update:visible": function(e) {
                        t.showTrajectoryList = e
                    },
                    "update:trajectoryList": function(e) {
                        t.trajectoryList = e
                    },
                    "update:trajectory-list": function(e) {
                        t.trajectoryList = e
                    },
                    "update:curTrajectory": function(e) {
                        t.curTrajectory = e
                    },
                    "update:cur-trajectory": function(e) {
                        t.curTrajectory = e
                    }
                }
            }), t._v(" "), t.showOperateTrajectory ? e("OperateTrajectoryDialog", {
                attrs: {
                    visible: t.showOperateTrajectory
                },
                on: {
                    "update:visible": function(e) {
                        t.showOperateTrajectory = e
                    },
                    confirm: t.createTrajectory
                }
            }) : t._e(), t._v(" "), e("RecordOrbitDialog", {
                ref: "dialogOrbit",
                attrs: {
                    visible: t.showRecordOrbit
                },
                on: {
                    "update:visible": function(e) {
                        t.showRecordOrbit = e
                    },
                    refresh: t.getTrajectoryList
                }
            })], 2)
        }
        ), [], !1, null, "9fff2bf6", null).exports
          , B = o(463)
          , q = {
            name: "ProductInfo",
            i18n: {
                messages: {
                    en: {
                        productInformation: "Product Information",
                        productModel: "Model",
                        armIp: "Robot IP",
                        firmwareVersion: "Firmware Version",
                        softwareVersion: "Software Version",
                        disconnectTitle: "Disconnect confirmation",
                        disconnectMsg: "Whether to disconnect from the robot arm?",
                        btnOk: "Disconnect",
                        btnCancel: "Cancle",
                        connect: "xArm Connect",
                        disconnect: "xArm Disconnect",
                        connecting: "Connecting",
                        connectIp: "Connect to Robot IP"
                    },
                    cn: {
                        productInformation: "产品信息",
                        productModel: "产品型号",
                        armIp: "机械臂IP",
                        firmwareVersion: "固件版本",
                        softwareVersion: "软件版本",
                        disconnectTitle: "断开确认",
                        disconnectMsg: "是否要断开和机械臂的连接",
                        btnOk: "断开",
                        btnCancel: "取消",
                        connect: "连接机械臂",
                        disconnect: "断开机械臂",
                        connecting: "正在连接",
                        connectIp: "连接 Robot IP"
                    }
                }
            },
            data: function() {
                return {
                    model: window.GlobalUtil.model,
                    infos: null,
                    isDev: !1,
                    warningFlag: !1,
                    addressList: [],
                    isSearch: !1,
                    showAddress: !1
                }
            },
            mounted: function() {
                this.getInfos(),
                setTimeout(this.getInfos, 1e3)
            },
            methods: {
                getInfos: function() {
                    var t = this.model.localSettingMgr.infoDatas || {}
                      , e = t.deviceInfo
                      , o = t.studioInfo
                      , r = e || {}
                      , n = r.device
                      , i = r.firmwareVersion
                      , a = (o || {}).version;
                    this.infos = {
                        productModel: {
                            label: this.$t("productModel"),
                            value: this.productModel
                        },
                        armIp: {
                            label: this.$t("armIp"),
                            value: n
                        },
                        firmwareVersion: {
                            label: this.$t("firmwareVersion"),
                            value: i
                        },
                        softwareVersion: {
                            label: this.$t("softwareVersion"),
                            value: a
                        }
                    }
                },
                onDisconnect: function() {
                    this.warningFlag = !1,
                    window.GlobalUtil.socketCom.sendCmd("disconnect_port", {
                        data: ""
                    }, (function(t) {
                        var e = t.data;
                        e ? console.log("disconnect_port", e) : console.log("disconnect error", t)
                    }
                    ))
                },
                searchAddress: function() {
                    var t = this;
                    if (!this.isSearch) {
                        this.isSearch = !0;
                        var e = this;
                        setTimeout((function() {
                            e.isSearch = !1
                        }
                        ), 5e3),
                        this.showAddress = !0,
                        window.GlobalUtil.socketCom.sendCmd("get_list_ports", {
                            data: ""
                        }, (function(e) {
                            var o = e.data;
                            t.isSearch = !1,
                            null != o && o.length > 0 ? (t.addressList = o,
                            console.log("addressList", t.addressList),
                            t.isConnected || (t.showAddress = !0)) : console.log("listPort error===", e)
                        }
                        ))
                    }
                },
                selectAdress: function(t) {
                    this.xArmAddress = t,
                    this.showAddress = !1
                },
                disconnect: function() {
                    window.GlobalUtil.socketCom.sendCmd("disconnect_port", {
                        data: ""
                    }, (function(t) {
                        var e = t.data;
                        e ? console.log("disconnect_port", e) : console.log("disconnect error", t)
                    }
                    ))
                },
                connect: function(t) {
                    this.isConnecting ? this.disconnect() : (this.showAddress = !1,
                    window.GlobalUtil.socketCom.sendCmd("connect_port", {
                        data: {
                            port: t
                        }
                    }, (function(t) {
                        t.data ? window.location.reload() : console.log("connect error", t)
                    }
                    )))
                }
            },
            computed: {
                productModel: function() {
                    var t = this.model.robot.state.info
                      , e = t.xarm_device_type
                      , o = t.xarm_axis
                      , r = "xArm" + e;
                    return 12 === this.model.robot.state.info.xarm_device_type && (r = 850),
                    6 == +o && 9 == +e && (r = "Lite 6"),
                    7 == +o && 13 == +e && (r = "xArm 7T"),
                    r
                },
                isConnected: function() {
                    return !0 === this.$store.state.robot.status.connected
                },
                isConnecting: function() {
                    return window.GlobalUtil.model.robot.state.info.xarm_is_connecting
                },
                xArmAddress: {
                    get: function() {
                        return this.$store.state.robot.info.port
                    },
                    set: function(t) {
                        var e = this.$store.state.robot.info;
                        e.port = t,
                        window.GlobalUtil.model.robot.state.info = e
                    }
                }
            }
        }
          , V = Object(a.a)(q, (function() {
            var t = this
              , e = t._self._c;
            return e("div", {
                staticClass: "product-info"
            }, [e("h3", {
                staticClass: "title font-vw-20"
            }, [t._v(t._s(t.$t("productInformation")))]), t._v(" "), e("div", {
                staticClass: "intro font-vw-18"
            }, t._l(t.infos, (function(o, r) {
                return e("div", {
                    key: o.lang,
                    staticClass: "column"
                }, [e("div", {
                    staticClass: "label"
                }, [t._v(t._s(o.label))]), t._v(" "), e("div", {
                    staticClass: "content"
                }, [t._v("\n        " + t._s(o.value) + "\n        "), "armIp" !== r || t.model.robot.state.info.server_is_xarm && !t.model.commonStatusMgr.debugMode ? t._e() : e("i", {
                    staticClass: "el-icon-question cursor-pointer",
                    on: {
                        click: function(e) {
                            t.warningFlag = !0
                        }
                    }
                })])])
            }
            )), 0), t._v(" "), e("el-dialog", {
                staticClass: "uf-dialog",
                attrs: {
                    visible: t.warningFlag,
                    "close-on-click-modal": !1,
                    "show-close": !1,
                    width: "25%",
                    center: ""
                },
                on: {
                    "update:visible": function(e) {
                        t.warningFlag = e
                    }
                }
            }, [e("div", {
                attrs: {
                    slot: "title"
                },
                slot: "title"
            }, [t._v(t._s(t.$t("disconnectTitle")))]), t._v(" "), e("div", {
                staticStyle: {
                    "text-align": "center"
                }
            }, [t._v("\n      " + t._s(t.$t("disconnectMsg")) + "\n    ")]), t._v(" "), e("span", {
                staticClass: "uf-space justify-center",
                attrs: {
                    slot: "footer"
                },
                slot: "footer"
            }, [e("button", {
                staticClass: "uf-button__cancel",
                on: {
                    click: function(e) {
                        t.warningFlag = !1
                    }
                }
            }, [t._v("\n        " + t._s(t.$t("btnCancel")) + "\n      ")]), t._v(" "), e("button", {
                staticClass: "uf-button",
                on: {
                    click: t.onDisconnect
                }
            }, [t._v(t._s(t.$t("btnOk")))])])]), t._v(" "), e("el-dialog", {
                staticClass: "uf-dialog",
                attrs: {
                    visible: !t.isConnected && (!t.model.robot.state.info.server_is_xarm || t.model.commonStatusMgr.debugMode),
                    "show-close": !1,
                    width: "25%",
                    center: ""
                }
            }, [e("div", {
                attrs: {
                    slot: "title"
                },
                slot: "title"
            }, [t._v(t._s(t.$t("connectIp")))]), t._v(" "), t.isConnected ? t._e() : e("div", {
                staticClass: "flex justify-between"
            }, [e("div", {
                staticClass: "flex search-box hpx-50"
            }, [e("el-input", {
                staticClass: "uf-input",
                attrs: {
                    type: "text",
                    disabled: t.isConnecting
                },
                on: {
                    keydown: function(e) {
                        return !e.type.indexOf("key") && t._k(e.keyCode, "enter", 13, e.key, "Enter") ? null : t.connect(t.xArmAddress)
                    }
                },
                model: {
                    value: t.xArmAddress,
                    callback: function(e) {
                        t.xArmAddress = e
                    },
                    expression: "xArmAddress"
                }
            }, [e("button", {
                staticClass: "hpx-50 wpx-30",
                attrs: {
                    slot: "prefix"
                },
                on: {
                    click: function(e) {
                        return t.searchAddress()
                    }
                },
                slot: "prefix"
            }, [e("i", {
                class: t.isSearch ? "el-icon-loading" : "el-icon-search"
            })])])], 1), t._v(" "), e("el-button", {
                staticClass: "uf-button hpx-40 connect-btn",
                on: {
                    click: function(e) {
                        return t.connect(t.xArmAddress)
                    }
                }
            }, [t._v("\n        " + t._s(t.$t(t.isConnecting ? "connecting" : "connect")) + "\n      ")])], 1), t._v(" "), e("ul", {
                directives: [{
                    name: "show",
                    rawName: "v-show",
                    value: t.showAddress,
                    expression: "showAddress"
                }],
                staticClass: "xArm-list scrollbar mt-px-20"
            }, t._l(t.addressList, (function(o, r) {
                return e("li", {
                    key: r,
                    on: {
                        click: function(e) {
                            return t.selectAdress(o.device)
                        }
                    }
                }, [t._v("\n        " + t._s(r % 2 == 0 ? "☆" : "★") + ": " + t._s(o.device) + "\n      ")])
            }
            )), 0)])], 1)
        }
        ), [], !1, null, "265e35e4", null).exports
          , z = o(462)
          , J = o(396)
          , Y = {
            name: "Controller",
            components: {
                CoordinateControl: z.a,
                TrackRecording: U,
                DeviceAdjustment: h.a,
                LiftPanel: z.b,
                ScriptPanel: z.c,
                RobotJobPanel:z.d
            },
            data: function() {
                return {}
            },
            methods: {}
        }
          , H = Object(a.a)(Y, (function() {
            var t = this
              , e = t._self._c;
            return e("div", {
                            staticClass: "controller"
                        }, 
                        [e("div", 
                            {staticClass: "container"}, 
                            [
                                e("ProductInfo"), 
                                t._v(" "), 
                                e("CoordinateControl"), 
                                t._v(" "), 
                                e("ScriptPanel"), 
                                t._v(" "), 
                                e("RobotJobPanel"), 
                                t._v(" "), 
                                e("ProductInfo"), 
                                t._v(" "), 
                                e("LiftPanel"), 
                                t._v(" "), 

                                e("ProductInfo"),
                                t._v(" "), 
                                e("DeviceAdjustment"), 
                                t._v(" "), 
                                e("ProductInfo"), 
                                t._v(" "), 
                                e("TrackRecording")
                            ], 1), 

                            t._v(" ")
                        ], 1
                    )
                }
        ), [], !1, null, "3c7ad43f", null);
        e.default = H.exports
    }
}]);
//# sourceMappingURL=control.b58ea3f.js.map
