# deepstream-save-images
Deepstream python apps with rtsp, usb and mipi source. Saves images when an object with unique tracking id is present in ROI.

This app is enabled for 2 streams for rtsp and video. so make necessary changes i the config files. add proper sources in the source.json for the same.

For mipi and usb only one source is enabled. so make necassary changes as well.

Only the frame is saved without the bounding boxes in "positive" folder when object is detected.
Every 10 secs an image is saved in the "negative" folder if there are no objects. This is mostly for data collection.
