# Scans and Transcriptions

Do you have a collection of scanned manuscripts or typescripts? Would you like them transcribed? This simple **desktop** application makes it easy to use external OCR/LLM services and local OCR models for this purpose. Once the transcriptions are complete, you can review, verify and correct them.

The process is straightforward: you prepare a folder containing scans of manuscripts, typescripts and old prints, and the application uses a selected provider to produce the transcriptions. Supported providers are Gemini API, local Ollama models, Mistral OCR API and Datalab Convert API. The application assists in the verification process by enabling visual comparison of the scans and transcriptions, providing named entity recognition (NER) — errors are more likely to occur in proper names — and tools for checking names on the scan. Finally, you will receive a folder containing the scans, the transcription files in TXT format and the metadata saved in JSON files.

Since the application can use paid API services, transcription may be subject to fees according to the provider's current pricing.

Gemini and Ollama providers use prompts and can be used for transcription, FIX, NER and BOX features. Mistral OCR and Datalab are OCR/conversion providers used for transcription only; they do not use the application's prompt and are not used for NER, FIX or BOX. The application can prepare transcriptions for a single image or a series of files.


## OCR and LLM providers:

  - **Gemini API**: cloud LLM/OCR provider. The Gemini key can be entered in the application settings and is stored in `config/config.json`.
  - **Ollama**: local provider for open-source vision/OCR models exposed through the local Ollama API. The default API endpoint is `http://localhost:11434/api`, and model names can be entered manually or loaded from a running Ollama instance.
  - **Mistral OCR API**: cloud OCR provider using the `mistral-ocr-4-1` model by default via the official Mistral Python SDK. The API key is read from the `MISTRAL_API_KEY` environment variable or from the `.env` file in the project root. It returns OCR output as Markdown; prompts are not sent to this provider.
  - **Datalab Convert API**: cloud document conversion provider. The API key is read from the `DATALAB_API_KEY` environment variable or from the `.env` file in the project root. Output format can be set to `markdown`, `html` or `json`; mode can be set to `fast`, `balanced` or `accurate`.


## Application features:

  - **Browsing scans and transcriptions**. The application assumes that the specified directory contains scan files and transcript files with identical names but with the *.txt extension. If a text file is missing, the application will automatically create an empty one.
  - If the folder does not yet contain scan images, you can **import pages from a PDF file**. The application can extract pages from PDF files and save them in the working folder as `img-001.png`, `img-002.png`, etc. The number width is chosen automatically from the total number of imported pages, so files sort correctly even for large PDFs. This is useful for digitised documents distributed as PDFs, with or without a text layer.
  - **Creating transcripts using the selected provider** for the current scan or scan series. For scan series, the application displays all scans in the selected directory and selects those that do not yet have a text transcript file or those with an empty transcript file. This selection can, of course, be changed.
  - Gemini batch transcription can run several API calls in parallel. The number of concurrent Gemini jobs is configurable in settings; low values are recommended when API rate limits are restrictive.
  - An LiteLLM endpoint can be selected for single-image and batch transcription. Configure its HTTPS endpoint and model in the LiteLLM settings tab; api key is read from the environment or the root `.env` file. The server model must support image input. FIX, NER and BOX are not supported by this provider.
  - Gemini and Ollama transcriptions use one of the **predefined prompts** (prompts for Polish documents are currently available), or a custom prompt prepared by the user. Mistral OCR and Datalab do not use these prompts.
  - Transcription files are automatically saved when you move to the next/previous file. You can also force a save by pressing the SAVE button.
  - Transcriptions can be saved as a **bulk text file**, a **Markdown file** with page separators such as `{959}------------------------------------------------`, or in a **docx file**. For docx files, the application also concatenates broken words and lines into paragraphs. Transcriptions can also be saved in **TEI-XML** format. HTML table transcriptions can be merged into a single HTML table, exported as separate HTML files next to the source TXT files, e.g. `img-001-1.html`, `img-001-2.html`, or saved to an XLSX workbook with one worksheet per table. XLSX export also tries to include the numbered caption immediately preceding each table, placing it below the table.
  - To facilitate verification of transcription accuracy, the application allows you to pan the scan using the left mouse button, **zoom in/out** using the mouse scroll wheel, and display a **magnifying glass** window at a selected location using the right mouse button. 
  - Simple **filters** can be applied to scans, such as contrast enhancement and image inversion.
  - You can adjust the font size in the transcription field.
  - Due to transcription errors often appearing in proper names (e.g. people, places and institutions), the option to **highlight** such words (**NER** button) has been added to draw attention to them during transcription verification. An experimental function (BOX button) **automatically marks entity names** in the scan. These names are marked with frames, and transcription text is placed above the frame, for a quick assessment of transcription accuracy. The size and position of the frames for entity names can be adjusted. The list of found **entity names can be exported to a CSV file** for further use. These tools require Gemini or Ollama.
  - The application **records API usage** for the current catalogue where usage metadata is available, providing information about the date, the model used, the number of tokens used (input and output), and the estimated cost of the call.
  - API/model calls have a configurable timeout. The final completion message for single-image recognition includes the recognition time.
  - The user interface supports **multiple language versions**. Currently, two languages are defined: **PL** and **EN** (see the localization.json file for definitions). 


## Screenshots and description:

The application window shows a magnifying glass with a visible enlargement of the fragment:

![Screen](/doc/screen_scan_transcript.png)

In the left panel containing the scan, you can move the image using the left mouse button, zoom in/out using the mouse wheel and enlarge a fragment using the magnifying glass icon and the right mouse button.

The application window is visible while the selected provider is reading the scan (the progress bar is visible at the top of the right panel and the provider button is unavailable while the model is processing the image).

![Screen](/doc/screen_scan_transcript_przetwarzanie.png)

In the right panel of the application, above the text field, there is a bar displaying the name of the scan directory that is currently being viewed (processed). To the right of this is a button that allows you to change the folder. Clicking this button displays a folder selection window and then loads the scan and transcription files (txt, if they are already in the folder). The same folder selection window appears automatically when the application starts.

![Screen](/doc/images_folder.png)

If the working folder is empty or if your source material is stored in a PDF file, you can also use the **PDF import** button. The application will extract the pages of the PDF as image files and save them in the current working folder, using names such as `img-001.png`, `img-002.png`, and so on. During import, a progress window is displayed, which is especially useful for large PDFs containing many hundreds of pages.

![PDF import](/doc/import_pdf.png)

**Main toolbar**:

List of buttons:

  - Go to the first file
  - Go to the previous file
  - Save changes to the current file
  - Read a scan with the selected provider
  - Read a series of scans with the selected provider
  - Open the export menu: merged TXT, merged Markdown, merged DOCX, merged HTML table, separate HTML table files, HTML tables to XLSX or TEI-XML
  - Go to the next file
  - Go to the last file

![Screen](/doc/toolbar.png)

Below the list of buttons, you will find information about the currently selected prompt file. The buttons on the right allow you to change, create or edit a prompt. When creating a new prompt, the application will suggest a template.

If there is no transcription file for the current file, an empty file will be created automatically. Transcription files can be edited manually. As well as saving via the 'SAVE' button, files are automatically saved when moving to the next or previous file, and when exiting the application.

You can also close the application using the Ctrl+Q shortcut.

**Transcription toolbar**:

![Screen](/doc/image_info.png)

Below is a bar displaying information about the current scan file, including its name, number in the series and the total number of scans in the folder. To the right are the 'A+' and 'A-' buttons, which are used to adjust the font size in the text field. Between the scan file name and the font size adjustment buttons is a search field for the transcription. After entering the required text and pressing Enter, the application highlights the matching text. You can also use the arrow button to start the search. The button with the 'x' symbol removes the highlights and clears the search field. The drop-down menu on the right allows you to change the interface language. Currently, Polish and English versions are available.

The 'NER', 'BOX' and 'CLS' buttons help to verify the transcription. Due to the high frequency of errors in proper names, these can be marked in the transcription text ('NER') and, for comparison, on the scan ('BOX'). The 'CLS' button clears the markings. The 'LEG' button displays a legend with descriptions of the colours used to mark different categories of proper names (people, places and organisations). NER, BOX, FIX and nominative CSV export require a prompt-capable provider: Gemini or Ollama.
The 'CSV' button allows you to export all the proper names found in the current catalogue to a CSV file.
The 'LOG' button displays a list of logged API/model calls. Cost and token counts are shown when the provider returns compatible usage metadata.
The 'FIX' button activates an experimental feature that verifies the existing transcription and highlights sections that may contain errors. This verification process uses the selected Gemini/Ollama correction model. The 'FIX2' button asks the correction model to compare the current transcription with the scan and replace the editor text with a corrected version, preserving Markdown/HTML structure where possible, including bold and italic markers.
  
**Reading a series of scans** by the selected provider:

![Screen](/doc/screen_scan_transcript_seria.png)

The file batch reading window displays all the scan files in the directory. You can select which files will be processed. By default, files for which there is no transcribed text file yet, or only an empty one, are selected. Buttons at the bottom of the window allow you to select or deselect all scans and initiate the transcription process for the selected scans. A progress bar will be displayed during this process (processing multiple files can be time-consuming).

Example of a **typescript transcription**:

![Screen](/doc/typescript_example.jpg)

**Prompt editor**:

![Screen](/doc/prompt_editor.jpg)

**Highlighting** of entity names in the transcription text:

![Screen](/doc/highlighting_entity_names.jpg)

Transcription errors produced by the LLM model often affect proper names. To make them easier to find, an experimental BOX function has been developed **to mark entity names in the scan**. The names are marked with frames and the name read by the model is placed above the frame. This allows you to quickly compare the name with the scan's actual content. See the screenshot below.

Although automatic marking is fairly accurate, it's possible to adjust the created frames. To do this, grab the frame with the left mouse button to move it or adjust its size. The lower right corner of the frame is a handle that allows you to resize it. The new position or size is automatically saved when you release the mouse button. However, it's important to remember that the main purpose of this feature is to indicate the position of the proper name for visual verification of transcription accuracy, even if the indication is inaccurate.

![Screen](/doc/entity_names_on_scan.jpg)

Here is an example of **colour coding** different categories of entity names (PERSON, LOCATION, ORGANISATION):

![Screen](/doc/entity_names_on_scan_2.jpg)

In some cases, manual reading of a scan can be facilitated by **image filters**. The following filters are available: negative and contrast. The screenshot below shows the negative filter in use:

![Screen](/doc/screen_scan_transcript_filtr.jpg)

Here is an example of the transcription of an **old print** from 18th-century Poland:

![Screen](/doc/screen_scan_transcript_print.jpg)

**API cost control** for the current catalogue:

![Screen](/doc/cost_control.jpg)

English **language version**:

![Screen](/doc/screen_english.jpg)

A 20th-century manuscript in German is shown here.

![Screen](/doc/screen_htr_ger.png)

Can modern OCR/LLM models read documents from the early 17^(th) century? A specialist should assess the number of errors and distortions – below is an example of a [letter](https://polona2.pl/item/list-marcina-glogowskiego-do-macieja-lubienskiego-8-luty-1608,OTIzNzMwMTA/0/#info:metadata) from 1608 (Marcin Głogowski to Maciej Łubieński).

![Screen](/doc/screen_glogowski_1608.jpg)

The 'test' folder contains sample scans and transcripts, including scans of manuscripts and typescripts from the 18th, 19th and 20th centuries, as well as old prints from the 18th century. Most of the documents are in Polish.

Please note that access to cloud providers via API **may be subject to a fee**, according to each provider's current pricing.

AI models were involved in the application programming :-)

The project was carried out at the Digital History Lab of the Institute of History at the Polish Academy of Sciences [https://ai.ihpan.edu.pl](https://ai.ihpan.edu.pl).

**Note**: A similar but more advanced transcription application (also using Python and TKinter!) is 
[Transcription Pearl](https://github.com/mhumphries2323/Transcription_Pearl) (Mark Humphries and Lianne C. Leddy, 2024. Transcription Pearl 1.0 Beta. Department of History: Wilfrid Laurier University.) – it allows you to use various models from OpenAI, Google, and Anthropic, import images from PDF files, etc. The same authors have another application: [ArchiveStudio](https://github.com/mhumphries2323/Archive_Studio), which is designed for the Windows system.


## Installation

Tested on Ubuntu. Ensure you have Python installed (version 3.10 or newer is recommended).
Install the required libraries:

```
pip install -r requirements.txt
```

System libraries are also required:

```
sudo apt install python3-dev
```

Run the application with:

```
python main.py
```

## Windows package

The scans_windows.zip package contains the python runtime, libraries and an application that can be started with the start.bat script.

## Configuration 

Open **Settings** in the application to choose the AI/OCR provider and configure provider-specific options.

**Gemini API**: enter your Gemini key in the application settings window. It will be saved in `config/config.json` under the `api_key` field. In the Gemini tab you can select separate models for transcription, FIX, NER/CSV and BOX.

**Ollama**: select `ollama` as the provider and configure the local API address and model names. The default endpoint is `http://localhost:11434/api`. The application can load the model list from a running Ollama instance. Ollama options also include HTML table post-processing: removing `<thead>` headers and pretty-printing HTML.

**Mistral OCR API**: select `mistral` as the provider. The API key is read from the `MISTRAL_API_KEY` environment variable or from the `.env` file in the project root (the same directory as `main.py`). The default OCR model is `mistral-ocr-4-1`. Mistral OCR does not use the prompt selected in the application. It returns OCR output as Markdown; table format can be set to Markdown or HTML.

**Datalab Convert API**: select `datalab` as the provider. The API key is read from the `DATALAB_API_KEY` environment variable or from the `.env` file in the project root. Output format can be set to `markdown`, `html` or `json`, and mode can be set to `fast`, `balanced` or `accurate`.

Example `.env` file:

```
MISTRAL_API_KEY=your-mistral-key
DATALAB_API_KEY=your-datalab-key
```

**Prompts**: The content of the instructions for the AI model (prompts) should be located in `.txt` files in the `prompt/` directory. This directory already contains sample prompts.

Prompts are used by Gemini and Ollama. Mistral OCR and Datalab Convert API do not receive the application's prompt; they work as OCR/conversion endpoints.

**Settings**: The application stores preferences (font size, user interface language, selected provider, provider parameters, timeout and streaming option) in `config/config.json`.

**Timeout and streaming**: The API/model timeout can be configured in settings. Gemini can stream transcription text into the editor while it is being generated. Ollama, Mistral and Datalab currently return a complete response after processing.

**PDF import**: The PDF-to-image import feature is implemented in Python using the `PyMuPDF` library, so it does not require separate system tools such as Poppler or `pdftoppm`.
