"""Native calibration on a frozen chat window snapshot; preview never calls models."""
import threading
import tempfile
from pathlib import Path
import objc
import AppKit as A
import Quartz as Q
from Foundation import NSObject, NSMakeRect, NSURL
from calibration import Calibration, validate_input_region
import ui_style
from perception import find_wechat_window, capture_image, capture_window, read_calibrated
from settings_config import read_document, write_settings
import userconfig

# The most common miss in real use was framing a single bubble instead of the whole
# message flow, and clicking 确认并启用 without knowing the preview gate unlocks it.
# Keep both facts in the texts the user actually reads while framing.
INSTRUCTIONS = {
    'messages': '消息区：一个框罩住整个消息流（双方头像与气泡都在内）；'
                '排除左侧列表、标题、公告和输入区。对方/我自动判定。',
    'input': '输入区：框选完整文字编辑区，排除底部工具栏和发送按钮。',
}
STATUS_FLOW = '① 框绿区（整个消息流）→ ② 框蓝区（输入区）→ ③ 预览识别 → ④ 通过后「确认并启用」点亮。'


class SelectionView(A.NSView):
    def isFlipped(self): return True

    def drawRect_(self, rect):
        self.picture.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            self.bounds(), NSMakeRect(0,0,0,0), A.NSCompositingOperationSourceOver, 1., True, None)
        A.NSColor.colorWithWhite_alpha_(0.,.30).set()
        mask = A.NSBezierPath.bezierPathWithRect_(self.bounds())
        regions=dict(self.owner.regions)
        regions[self.owner.mode]=self.selection
        for selection in regions.values():
            if selection:mask.appendBezierPathWithRect_(NSMakeRect(*selection))
        mask.setWindingRule_(A.NSEvenOddWindingRule)
        mask.fill()
        for key,selection in regions.items():
            if not selection:continue
            color=ui_style.PALETTE['green'] if key=='messages' else ui_style.rgb(0x2878CD)
            color.set()
            border=A.NSBezierPath.bezierPathWithRect_(NSMakeRect(*selection))
            border.setLineWidth_(2.5 if key==self.owner.mode else 1.5);border.stroke()
            label='消息识别区域' if key=='messages' else '输入区域'
            A.NSString.stringWithString_(label).drawAtPoint_withAttributes_(
                (selection[0]+4,selection[1]+4),
                {A.NSFontAttributeName:A.NSFont.boldSystemFontOfSize_(11),
                 A.NSForegroundColorAttributeName:color,
                 A.NSBackgroundColorAttributeName:A.NSColor.whiteColor()})
        for m in self.messages:
            r=NSMakeRect(m.x*self.bounds().size.width,m.y*self.bounds().size.height,
                         m.w*self.bounds().size.width,m.h*self.bounds().size.height)
            (ui_style.PALETTE["amber"] if m.side=='unknown' else ui_style.PALETTE["green"]).set()
            A.NSBezierPath.bezierPathWithRect_(r).stroke()
            label={'them':'对方','me':'我','unknown':'未确认'}[m.side]
            A.NSString.stringWithString_(label).drawAtPoint_withAttributes_(
                (r.origin.x,max(0,r.origin.y-14)),
                {A.NSFontAttributeName:A.NSFont.boldSystemFontOfSize_(11),
                 A.NSForegroundColorAttributeName:ui_style.PALETTE["text"],
                 A.NSBackgroundColorAttributeName:A.NSColor.whiteColor()})

    def mouseDown_(self, event):
        if self.owner.busy: return
        p=self.convertPoint_fromView_(event.locationInWindow(),None)
        self.anchor=(p.x,p.y); self.edge=None
        if self.selection:
            x,y,w,h=self.selection
            for name,value,current in [('left',x,p.x),('right',x+w,p.x),('top',y,p.y),('bottom',y+h,p.y)]:
                if abs(value-current)<7:
                    self.edge=name; break
        self.owner.invalidate()

    def mouseDragged_(self, event):
        if self.owner.busy or not hasattr(self,'anchor'): return
        p=self.convertPoint_fromView_(event.locationInWindow(),None)
        px=max(0,min(p.x,self.bounds().size.width)); py=max(0,min(p.y,self.bounds().size.height))
        if self.edge:
            x,y,w,h=self.selection; r,b=x+w,y+h
            if self.edge=='left': x=min(px,r-5)
            if self.edge=='right': r=max(px,x+5)
            if self.edge=='top': y=min(py,b-5)
            if self.edge=='bottom': b=max(py,y+5)
            self.selection=(x,y,r-x,b-y)
        else:
            x,y=self.anchor
            self.selection=(min(x,px),min(y,py),abs(px-x),abs(py-y))
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        self.mouseDragged_(event)


class CalibrationController(NSObject):
    @objc.python_method
    # 纯原生窗口装配：依赖真聊天窗口/截图权限，离线回归只测文案常量与
    # 交互逻辑（见 tests/test_calibration_ui_copy.py），装配体本身不测。
    def build(self, callback, saved='', image=None, win=None, saved_input=''):  # pragma: no cover
        self.mode = 'messages'
        self.regions = {'messages': None, 'input': None}
        self.callback=callback; self.busy=False; self.preview=None; self.closed=False
        self.win=win or find_wechat_window()
        if self.win is None: raise ValueError('请先打开聊天窗口。')
        self.image=image if image is not None else capture_image(self.win.wid)
        if self.image is None:
            with tempfile.TemporaryDirectory() as d:
                path=Path(d)/'window.png'
                if capture_window(self.win.wid,path):
                    source=Q.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)),None)
                    self.image=Q.CGImageSourceCreateImageAtIndex(source,0,None) if source else None
        if self.image is None: raise ValueError('无法读取聊天窗口，请检查屏幕录制权限。')
        self.path=userconfig.env_files()[0]
        self.original=read_document(self.path)
        palette = ui_style.PALETTE
        screen=A.NSScreen.mainScreen().visibleFrame().size
        scale=min(1.,min(1000,screen.width-96)/self.win.w,
                  min(660,screen.height-300)/self.win.h)
        cw,ch=self.win.w*scale,self.win.h*scale
        width=max(760,cw+48); height=ch+244
        self.window=A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0,0,width,height),A.NSWindowStyleMaskTitled|A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered,False)
        self.window.setReleasedWhenClosed_(False); self.window.setDelegate_(self)
        title = '校准区域'
        self.window.setTitle_(title+' · 确认后立即生效')
        self.window.setLevel_(A.NSFloatingWindowLevel+1)
        self.window.setAppearance_(A.NSAppearance.appearanceNamed_(A.NSAppearanceNameAqua))
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(A.NSColor.clearColor())
        self.window.setHasShadow_(True)
        view=A.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0,0,width,height))
        view.setMaterial_(A.NSVisualEffectMaterialSidebar)
        view.setBlendingMode_(A.NSVisualEffectBlendingModeBehindWindow)
        view.setState_(A.NSVisualEffectStateActive)
        view.setWantsLayer_(True)
        reduced=A.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency()
        surface=ui_style.SOLID_PALETTE if reduced else palette
        view.layer().setBackgroundColor_(surface['bg'].CGColor())
        self.window.setContentView_(view)
        view.addSubview_(ui_style.make_label(title,24,height-52,width-48,30,22,bold=True))
        self.selector=A.NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(24,height-86,330,26))
        self.selector.setSegmentCount_(2)
        for index,label in enumerate(('消息识别区域 · 绿框','输入区域 · 蓝框')):
            self.selector.setLabel_forSegment_(label,index)
            self.selector.setWidth_forSegment_(165,index)
        self.selector.setSelectedSegment_(0)
        self.selector.setTarget_(self);self.selector.setAction_('regionChanged:')
        self.selector.setAccessibilityLabel_('选择当前要调整的区域')
        view.addSubview_(self.selector)
        self.instruction=ui_style.make_label(INSTRUCTIONS['messages'],
            24,height-112,width-48,20,12,palette['muted'])
        view.addSubview_(self.instruction)
        card=ui_style.make_surface(12,surface['surface'],surface['edge'])
        card.setFrame_(NSMakeRect(16,116,width-32,ch+16));view.addSubview_(card)
        self.canvas=SelectionView.alloc().initWithFrame_(NSMakeRect((width-cw)/2,124,cw,ch))
        self.canvas.picture=A.NSImage.alloc().initWithCGImage_size_(self.image,(cw,ch))
        self.canvas.owner=self;self.canvas.selection=None;self.canvas.messages=[]
        self.canvas.setAccessibilityLabel_('聊天窗口截图，拖动框选区域，拖动边缘调整')
        view.addSubview_(self.canvas)
        for key,value in (('messages',saved),('input',saved_input)):
            if value:
                try:
                    c=Calibration.parse(value)
                    if c.matches(self.win):self.regions[key]=(c.x*scale,c.y*scale,c.width*scale,c.height*scale)
                except (ValueError,TypeError):pass
        self.canvas.selection=self.regions['messages']
        notice=ui_style.make_surface(10,palette['amber'].colorWithAlphaComponent_(.10),
                                    palette['amber'].colorWithAlphaComponent_(.18))
        notice.setFrame_(NSMakeRect(24,64,width-48,42));view.addSubview_(notice)
        self.status=ui_style.make_label(STATUS_FLOW,
            36,74,width-72,22,12,palette['accent'],bold=True)
        view.addSubview_(self.status)
        hint='两区一起保存 · 不发送消息、不清空草稿'
        view.addSubview_(ui_style.make_label(hint,24,18,width-400,30,11,palette['muted']))
        for title,action,x in [('取消','cancel:',width-364),('预览识别','preview:',width-248),('确认并启用','save:',width-132)]:
            button=A.NSButton.buttonWithTitle_target_action_(title,self,action)
            button.setFrame_(NSMakeRect(x,18,108,32))
            ui_style.style_button(button,font_size=12,primary=action=='save:')
            view.addSubview_(button)
            if action=='save:':self.save_button=button;button.setEnabled_(False);button.setKeyEquivalent_('\r')
            button.setToolTip_('「预览识别」通过后才会启用；两区一起保存'
                               if action=='save:' else None)
            if action=='preview:':
                self.preview_button=button

            if action=='cancel:':button.setKeyEquivalent_('\x1b')
        self.window.center();self.window.makeKeyAndOrderFront_(None)
        A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        return self

    def regionChanged_(self, sender):
        if self.busy:
            self.selector.setSelectedSegment_(0 if self.mode=='messages' else 1)
            return
        self.regions[self.mode]=self.canvas.selection
        self.mode='messages' if self.selector.selectedSegment()==0 else 'input'
        self.canvas.selection=self.regions[self.mode]
        self.instruction.setStringValue_(INSTRUCTIONS[self.mode])
        self.canvas.setNeedsDisplay_(True)

    @objc.python_method
    def invalidate(self):
        self.preview=None;self.canvas.messages=[];self.save_button.setEnabled_(False)
        regions=dict(self.regions); regions[self.mode]=self.canvas.selection
        if all(regions.values()):
            self.status.setStringValue_('选区已调整——重新「预览识别」，通过后「确认并启用」才会点亮。')
        else:
            missing='输入区（蓝框）' if not regions['input'] else '消息区（绿框）'
            self.status.setStringValue_(f'还差{missing}；两个区都框好后先「预览识别」。')

    @objc.python_method
    def selection(self):
        if not self.canvas.selection: raise ValueError('请先框选当前区域。')
        scale=self.canvas.bounds().size.width/self.win.w
        return Calibration(self.win.w,self.win.h,*(v/scale for v in self.canvas.selection))

    def preview_(self,sender):
        if self.busy:return
        try:
            self.regions[self.mode]=self.canvas.selection
            if not all(self.regions.values()):
                raise ValueError('请分别框选消息区和输入区，再一起预览。')
            scale=self.canvas.bounds().size.width/self.win.w
            message, editor = (Calibration(self.win.w,self.win.h,*(v/scale for v in self.regions[k]))
                               for k in ('messages','input'))
            validate_input_region(message,editor)
            c=(message,editor)
        except ValueError as e:self.status.setStringValue_(str(e));return
        self.busy=True;self.save_button.setEnabled_(False);self.preview_button.setEnabled_(False)
        self.status.setStringValue_('正在预览消息识别并检查输入区，请稍候…')
        def run():
            try:
                win=dict(wid=self.win.wid,w=self.win.w,h=self.win.h,x=self.win.x,y=self.win.y,title=self.win.title)
                res=read_calibrated(self.image,c[0],win,100)
                result=(c,res,None)
            except Exception as e:result=(c,None,type(e).__name__)
            self.performSelectorOnMainThread_withObject_waitUntilDone_('previewDone:',result,False)
        threading.Thread(target=run,daemon=True).start()

    def previewDone_(self,result):
        if self.closed:return
        self.busy=False;self.preview_button.setEnabled_(True)
        c,res,error=result
        if error:self.status.setStringValue_('识别失败：'+error);return
        self.preview=c;self.canvas.messages=res['messages'];self.canvas.setNeedsDisplay_(True)
        counts={s:sum(m.side==s for m in res['messages']) for s in ('them','me','unknown')}
        self.status.setStringValue_(f"预览：对方 {counts['them']} · 我 {counts['me']} · 未确认 {counts['unknown']}。确认选区后启用；未确认不自动回复。")

        self.save_button.setEnabled_(True)

    def save_(self,sender):
        if self.preview is None or self.busy:return
        current=find_wechat_window(self.win.wid)
        if current is None or current.wid!=self.win.wid or not self.preview[0].matches(current):
            self.status.setStringValue_('聊天窗口已改变，请取消后重新校准。');return
        try:
            write_settings(self.path,self.original,{'JEV_MESSAGE_REGION':self.preview[0].serialize(),
                                                     'JEV_INPUT_REGION':self.preview[1].serialize()})
        except (ValueError,OSError) as e:self.status.setStringValue_(str(e));return
        self.callback(self.preview,self.win.wid);self.closed=True;self.window.close()

    def cancel_(self,sender):self.window.close()

    def windowWillClose_(self,notification):
        if not self.closed:
            self.closed=True;self.callback(None,None)
