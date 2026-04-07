import tkinter as tk
from tkinter import filedialog
from tkinter import *
from PIL import Image, ImageTk, ImageOps, ImageDraw, ImageFont
from pdf2image import convert_from_path
import os
from openpyxl import Workbook

# ── Configuration ────────────────────────────────────────────────────────────
FONT_PATH         = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
POPPLER_PATH      = "/opt/homebrew/bin/"
CHECK_IMAGE_PATH  = "/Users/danny/mu_code/images/green_check.png"
WRONG_ANS_FOLDER  = "/Users/danny/mu_code/wrong answers"
SAVE_DIRECTORY    = "/Users/danny/Documents/AVS/Test results"
# ─────────────────────────────────────────────────────────────────────────────


class PDFtoImageConverter:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Grader")
        self.root.geometry("1100x1000")

        self.pdf_path = ""
        self.insert_image_path = CHECK_IMAGE_PATH
        self.history = []
        self.historymask = []
        self.images = []
        self.current_image_index = 0
        self.image = None   # PIL image in natural (un-zoomed) size — all drawing happens here
        self.mask = None

        # Zoom
        self.zoom_level = 1.0

        # Point-tracking state
        self.left_click_count = 0
        self.left_click_history = []
        self.half_check_history = []

        # Right-click menu coordinates stored in IMAGE space
        self.menu_x = None
        self.menu_y = None

        # Scores accumulated across pages
        self.user_variables = {}

        self._build_ui()
        self._build_menu()
        self._apply_theme()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.button_frame = tk.Frame(self.root)
        self.button_frame.grid(row=0, column=0, pady=10, sticky="w")

        self.select_button = tk.Button(
            self.button_frame, text="Select test", command=self.select_pdf
        )
        self.select_button.grid(row=0, column=0)

        self.export_button = tk.Button(
            self.button_frame, text="Export", command=self.export_data
        )
        self.export_button.grid(row=0, column=2, padx=10)


        self.who_is_this_label = tk.Label(self.button_frame, text="Who is this?")
        self.who_is_this_label.grid(row=1, column=0, pady=5)

        self.who_is_this_entry = tk.Entry(self.button_frame)
        self.who_is_this_entry.grid(row=1, column=1, pady=5)

        # ── Scrollable canvas (no fixed size — fills the window) ──
        canvas_frame = tk.Frame(self.root)
        canvas_frame.grid(row=2, column=0, sticky="nsew")
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_frame, bg="black")
        v_scroll = tk.Scrollbar(canvas_frame, orient="vertical",   command=self.canvas.yview)
        h_scroll = tk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        v_scroll.pack(side="right",  fill="y")
        h_scroll.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.canvas.focus_set()

        self.canvas.bind("<Button-1>", self.insert_image)
        self.canvas.bind("<Button-3>", self.save_image)
        self.root.bind("<Button-2>",   self.show_menu)
        self.root.bind("<Command-z>",  self.undo)

        # Plain scroll to zoom — zooms about the cursor position
        self.canvas.bind("<MouseWheel>", self.mouse_zoom)   # Mac / Windows
        self.canvas.bind("<Button-4>",   self.mouse_zoom)   # Linux scroll up
        self.canvas.bind("<Button-5>",   self.mouse_zoom)   # Linux scroll down

    def _build_menu(self):
        self.wrong_answer_images = [
            f for f in os.listdir(WRONG_ANS_FOLDER) if f.endswith(".png")
        ]
        self.menu = tk.Menu(self.root, tearoff=0)
        for img_file in self.wrong_answer_images:
            self.menu.add_command(
                label=img_file,
                command=lambda img=img_file: self.menu_action(img),
            )
        self.menu.add_separator()
        self.menu.add_command(label="Add Custom Text", command=self.add_custom_text)

    def _apply_theme(self):
        for widget in (
            self.root,
            self.canvas,
            self.button_frame,
            self.who_is_this_label,
            self.who_is_this_entry,
        ):
            widget.configure(bg="black")
        for widget in (self.select_button, self.export_button):
            widget.configure(bg="black", fg="black")

    # ── Coordinate mapping ────────────────────────────────────────────────────

    def _canvas_to_image_coords(self, x, y):
        """
        Convert a canvas mouse position to the corresponding PIL image position,
        accounting for both scroll offset and current zoom level.
        All drawing operations must use these coordinates.
        """
        cx = self.canvas.canvasx(x)  # adjust for horizontal scroll
        cy = self.canvas.canvasy(y)  # adjust for vertical scroll
        return int(cx / self.zoom_level), int(cy / self.zoom_level)

    # ── PDF / image display ───────────────────────────────────────────────────

    def select_pdf(self):
        self.pdf_path = filedialog.askopenfilename(
            defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")]
        )
        if self.pdf_path:
            self.images = convert_from_path(self.pdf_path, poppler_path=POPPLER_PATH)
            if self.images:
                self.current_image_index = 0
                self.zoom_level = 1.0
                self.display_image(self.images[0])

    def display_next_image(self):
        if self.current_image_index < len(self.images) - 1:
            self.current_image_index += 1
            self.zoom_level = 1.0
            self.display_image(self.images[self.current_image_index])

    def display_image(self, image):
        max_size = (800, 800)
        image.thumbnail(max_size, Image.LANCZOS)
        inverted_image = ImageOps.invert(image.convert("RGB"))

        self.image = inverted_image   # natural-size PIL image; never zoomed
        self._create_mask()
        self._render()

    def _render(self):
        """
        Scale self.image to the current zoom level for display only.
        self.image itself is always kept at natural (1×) size so that
        stamps and text are drawn at the correct resolution.
        """
        if self.image is None:
            return
        w = int(self.image.width  * self.zoom_level)
        h = int(self.image.height * self.zoom_level)
        display = self.image.resize((w, h), Image.LANCZOS)
        tk_image = ImageTk.PhotoImage(display)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=tk_image)
        self.canvas.image = tk_image                        # keep reference alive
        self.canvas.configure(scrollregion=(0, 0, w, h))

    # ── Zoom ──────────────────────────────────────────────────────────────────

    def mouse_zoom(self, event):
        """Zoom in/out centred on the mouse cursor position."""
        if self.image is None:
            return

        old_zoom = self.zoom_level

        if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
            new_zoom = min(round(self.zoom_level + 0.25, 2), 4.0)
        else:
            new_zoom = max(round(self.zoom_level - 0.25, 2), 0.25)

        if new_zoom == old_zoom:
            return

        # Canvas-space position of the cursor (accounts for current scroll)
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)

        self.zoom_level = new_zoom
        self._render()

        # After re-render, scroll so that the image point under the cursor
        # stays under the cursor.  new_cx/cy is where that point now lives.
        new_w = self.image.width  * new_zoom
        new_h = self.image.height * new_zoom
        new_cx = cx * (new_zoom / old_zoom)
        new_cy = cy * (new_zoom / old_zoom)

        # xview_moveto expects a fraction of the total scroll region
        self.canvas.xview_moveto(max(0, (new_cx - event.x) / new_w))
        self.canvas.yview_moveto(max(0, (new_cy - event.y) / new_h))

    # ── Mask ──────────────────────────────────────────────────────────────────

    def _create_mask(self):
        if self.image:
            self.mask = Image.new("RGBA", self.image.size, (0, 0, 0, 0))

    # ── Undo ──────────────────────────────────────────────────────────────────

    def undo(self, event):
        if not self.history:
            return

        self.image = self.history.pop()
        self.mask  = self.historymask.pop()

        if self.left_click_history and self.left_click_history[-1]:
            self.left_click_count -= 1
        elif self.half_check_history and self.half_check_history[-1]:
            self.left_click_count -= 0.5

        self.left_click_history.pop()
        self.half_check_history.pop()

        self._render()

    # ── Stamp helpers ─────────────────────────────────────────────────────────

    def _load_and_invert_stamp(self, image_path):
        """Open a PNG stamp, invert its RGB channels, and return it as RGBA."""
        stamp = Image.open(image_path).convert("RGBA")
        r, g, b, a = stamp.split()
        inv_rgb = ImageOps.invert(Image.merge("RGB", (r, g, b)))
        return Image.merge("RGBA", (*inv_rgb.split(), a))

    def _resize_to_third(self, image):
        return image.resize(
            (image.width // 3, image.height // 3), Image.LANCZOS
        )

    def _save_state(self):
        self.history.append(self.image.copy())
        self.historymask.append(self.mask.copy())

    def _paste_stamp(self, stamp, x, y, center=True):
        """x, y must be in IMAGE coordinates (not canvas/zoom coordinates)."""
        if center:
            x -= stamp.width  // 2
            y -= stamp.height // 2
        self.mask.paste(stamp, (x, y), stamp)
        self.image.paste(stamp, (x, y), stamp)
        self._render()

    # ── Left-click: insert check mark ─────────────────────────────────────────

    def insert_image(self, event):
        if not self.pdf_path or not self.canvas.image:
            return

        self._save_state()
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        stamp = self._resize_to_third(
            self._load_and_invert_stamp(self.insert_image_path)
        )
        self._paste_stamp(stamp, ix, iy)

        self.left_click_count += 1
        self.left_click_history.append(True)
        self.half_check_history.append(False)

    # ── Right-click: save and advance ─────────────────────────────────────────

    def save_image(self, event):
        if not self.pdf_path or not self.canvas.image:
            return

        user_text = self.who_is_this_entry.get()
        if not user_text:
            return

        filename  = f"graded{self.current_image_index}.png"
        save_path = os.path.join(SAVE_DIRECTORY, filename)

        # Save only the annotation mask layer, re-inverted to normal colours
        mask_rgb = self.mask.convert("RGB")
        ImageOps.invert(mask_rgb).save(save_path)

        self.user_variables[user_text] = self.left_click_count

        self.display_next_image()

        # Reset per-page state
        self.left_click_count    = 0
        self.left_click_history  = []
        self.half_check_history  = []
        self.who_is_this_entry.delete(0, "end")

    # ── Middle-click menu ─────────────────────────────────────────────────────

    def show_menu(self, event):
        # Always store in image coordinates so menu actions place correctly
        self.menu_x, self.menu_y = self._canvas_to_image_coords(event.x, event.y)
        self.menu.post(event.x_root, event.y_root)

    def menu_action(self, img_file):
        if self.menu_x is None or self.menu_y is None:
            return

        self._save_state()
        stamp = self._resize_to_third(
            self._load_and_invert_stamp(os.path.join(WRONG_ANS_FOLDER, img_file))
        )
        self._paste_stamp(stamp, self.menu_x, self.menu_y)

        self.left_click_history.append(False)

        if img_file == "0.5 check.png":
            self.left_click_count += 0.5
            self.half_check_history.append(True)
        else:
            self.half_check_history.append(False)

    def add_custom_text(self):
        def submit_text():
            entered_text = text_entry.get("1.0", tk.END).strip()
            if entered_text:
                self.menu.add_command(
                    label=entered_text,
                    command=lambda txt=entered_text: self.insert_text(txt),
                )
                top.destroy()
                self.insert_text(entered_text)

        top = tk.Toplevel(self.root)
        top.title("Enter custom text")
        text_entry = tk.Text(top, wrap=tk.WORD)
        text_entry.pack(expand=True, fill=tk.BOTH)
        text_entry.focus_set()
        tk.Button(top, text="Submit", command=submit_text).pack(pady=5)

    def insert_text(self, text):
        if self.menu_x is None or self.menu_y is None:
            return

        self._save_state()
        font      = ImageFont.truetype(FONT_PATH, 20)
        draw      = ImageDraw.Draw(self.image)
        drawmask  = ImageDraw.Draw(self.mask)
        # menu_x/y are already in image coordinates
        draw.text((self.menu_x, self.menu_y),     text, font=font, fill="cyan")
        drawmask.text((self.menu_x, self.menu_y), text, font=font, fill="cyan")
        self._render()

        self.left_click_history.append(False)
        self.half_check_history.append(False)

    # ── Export ────────────────────────────────────────────────────────────────

    def export_data(self):
        # Write scores to Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "User Variables"
        ws.append(["Name", "Score"])
        for key, value in self.user_variables.items():
            ws.append([key, value])
        wb.save("user_variables.xlsx")

        # Merge saved PNGs into a single PDF
        image_files = sorted(
            f for f in os.listdir(SAVE_DIRECTORY) if f.endswith(".png")
        )
        image_list = [
            Image.open(os.path.join(SAVE_DIRECTORY, f)).convert("RGB")
            for f in image_files
        ]

        if image_list:
            image_list[0].save(
                "graded_images.pdf",
                save_all=True,
                append_images=image_list[1:],
            )
            print("Saved graded_images.pdf")
        else:
            print("No graded images found.")


if __name__ == "__main__":
    root = tk.Tk()
    app = PDFtoImageConverter(root)
    root.mainloop()
