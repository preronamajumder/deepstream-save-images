# deepstream-save-images
Deepstream python apps with rtsp, usb and mipi source. Saves images when an object with unique tracking id is present in ROI. If you want to disable ROI then put roi-RF=0;0;0;480;640;480;640;0 in config_nvanalytics.txt for processing size of (640,480)

This app is enabled for 2 streams for rtsp and video. so make necessary changes in the config files for different number of sources. Add proper sources in the config.json for the same.

For mipi and usb only one source is enabled. So make necassary changes as well.

Only the frame is saved without the bounding boxes in "positive" folder when object is detected.
Every 10 secs an image is saved in the "negative" folder if there are no objects. This is mostly for data collection.
