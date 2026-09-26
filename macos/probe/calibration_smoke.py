"""Native calibration smoke test using a supplied screenshot, never live credentials.

Example: python -B probe/calibration_smoke.py screenshot.png --window 1039 1055
         --region 672 51 361 799 --input-region 684 865 336 140
Coordinates are window points. Does not save to the real user env or call a model.
"""
import argparse
import sys,time,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pathlib import Path
from unittest.mock import patch,Mock
import AppKit as A,Quartz as Q
from Foundation import NSDate,NSURL
import userconfig
app=A.NSApplication.sharedApplication();app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
with patch.object(userconfig,'load'),patch.object(userconfig,'get',return_value=''):
 import hud
 # 上游这里还要 patch 掉 make_judge（本地判断模型会真去加载权重）；判断层已删，只剩生成层
 with patch.object(hud,'Generator',return_value=Mock()):
  h=hud.HudController.alloc().init()
h._paused=True
assert h._calibration is None and not h._calibrating
from calibration_ui import CalibrationController
from perception import WindowInfo
parser=argparse.ArgumentParser()
parser.add_argument('image')
parser.add_argument('--window',type=float,nargs=2,required=True)
parser.add_argument('--region',type=float,nargs=4,required=True)
parser.add_argument('--input-region',type=float,nargs=4,required=True)
args=parser.parse_args()
f=str(Path(args.image).resolve())
s=Q.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(f),None);i=Q.CGImageSourceCreateImageAtIndex(s,0,None)
win=WindowInfo(99,1,'微信',0,0,*args.window)
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'env';p.write_text('# test\nCUSTOM=preserved\n')
 with patch.object(userconfig,'env_files',return_value=[p]),patch('calibration_ui.find_wechat_window',return_value=win),patch('calibration_ui.capture_image',return_value=i):
  h.calibration_button.performClick_(None)
 c=h.calibration_controller
 assert h._calibrating
 scale=c.canvas.bounds().size.width/win.w
 # Exercise the actual native drag handlers, not just the stored rectangle.
 c.canvas.selection=None
 x,y,w,height=(v*scale for v in args.region)
 start=c.canvas.convertPoint_toView_((x,y),None)
 end=c.canvas.convertPoint_toView_((x+w,y+height),None)
 c.canvas.mouseDown_(Mock(locationInWindow=Mock(return_value=start)))
 c.canvas.mouseDragged_(Mock(locationInWindow=Mock(return_value=end)))
 c.canvas.mouseUp_(Mock(locationInWindow=Mock(return_value=end)))
 assert all(abs(a-b)<.01 for a,b in zip(c.canvas.selection,(x,y,w,height)))
 # Both areas are edited on the same canvas, with no intermediate write.
 assert p.read_text()=='# test\nCUSTOM=preserved\n'
 c.preview_(None)
 assert c.preview is None and not c.save_button.isEnabled()
 c.selector.setSelectedSegment_(1);c.regionChanged_(None)
 c.canvas.selection=tuple(v*scale for v in args.input_region)
 c.invalidate()
 c.selector.setSelectedSegment_(0);c.regionChanged_(None)
 assert all(c.regions.values())
 c.preview_(None)
 limit=time.monotonic()+30
 while c.busy and time.monotonic()<limit:
  A.NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.05))
 assert c.preview and c.save_button.isEnabled()
 # Render native content with its explicit background, controls and status.
 v=c.window.contentView();v.display();rep=v.bitmapImageRepForCachingDisplayInRect_(v.bounds());v.cacheDisplayInRect_toBitmapImageRep_(v.bounds(),rep)
 rep.representationUsingType_properties_(A.NSBitmapImageFileTypePNG,{}).writeToFile_atomically_('/tmp/jev-calibration-ui.png',True)
 with patch('calibration_ui.find_wechat_window',return_value=win):c.save_(None)
 assert h._calibration and h._calibration_wid==99 and not h._calibrating
 assert 'CUSTOM=preserved' in p.read_text() and p.stat().st_mode&0o777==0o600
 assert h._input_calibration and h._input_calibration_wid==win.wid
 assert 'JEV_INPUT_REGION=' in p.read_text()
 # The new header control is a native symbol, does not overlap the title, and survives collapse.
 h._render('chat','大白 VIP 交流群（123）')
 h.panel.orderFrontRegardless()
 assert h.calibration_button.image() is not None
 assert h.rows['chat'].frame().origin.x+h.rows['chat'].frame().size.width <= h.calibration_button.frame().origin.x
 v=h.panel.contentView();v.display();rep=v.bitmapImageRepForCachingDisplayInRect_(v.bounds());v.cacheDisplayInRect_toBitmapImageRep_(v.bounds(),rep)
 rep.representationUsingType_properties_(A.NSBitmapImageFileTypePNG,{}).writeToFile_atomically_('/tmp/jev-calibration-header.png',True)
 h._set_collapsed(True)
 assert not h.calibration_button.isHidden()
 h._set_collapsed(False)
 before=h._calibration
 with patch.object(userconfig,'env_files',return_value=[p]),patch('calibration_ui.find_wechat_window',return_value=win),patch('calibration_ui.capture_image',return_value=i):
  h.calibrateMessages_(None)
 h.calibration_controller.cancel_(None)
 assert h._calibration==before and not h._calibrating
 h.reanalyze_(None)
 assert h._calibration==before,'reanalyze must preserve calibration'
 with patch.object(userconfig,'env_files',return_value=[p]):h.clearCalibration_(None)
 assert h._calibration is None and not h._calibration_required
 print('PASS unified calibration, atomic save, cancel, header icon and collapsed mode; no real credentials changed')
h.panel.orderOut_(None);h._ov_panel.orderOut_(None)
