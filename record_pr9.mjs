import puppeteer from 'puppeteer';
import { PuppeteerScreenRecorder } from 'puppeteer-screen-recorder';
import path from 'path';

async function recordPage(url, outputPath) {
    console.log(`Starting interactive headless recording for ${url}...`);
    const browser = await puppeteer.launch();
    const page = await browser.newPage();

    // Set a compact viewport for the extension mockup
    await page.setViewport({ width: 800, height: 600 });

    const recorder = new PuppeteerScreenRecorder(page, {
        fps: 20,
        quality: 100,
        videoFrame: { width: 800, height: 600 }
    });

    const fileUri = 'file:///' + path.resolve('tools/synth-overlay/mock_polymarket_interactive.html').replace(/\\/g, '/');
    await page.goto(fileUri, { waitUntil: 'domcontentloaded' });

    // Inject fake cursor tracker
    await page.evaluate(() => {
        const cursor = document.createElement('div');
        cursor.id = 'puppeteer-cursor';
        cursor.style.position = 'absolute';
        cursor.style.width = '24px';
        cursor.style.height = '24px';
        cursor.style.backgroundImage = 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'24\' height=\'24\' viewBox=\'0 0 24 24\' fill=\'white\' stroke=\'black\' stroke-width=\'1.5\'><path d=\'M5.5 3.21V20.8c0 .45.54.67.85.35l4.86-4.86a.5.5 0 0 1 .35-.15h6.87a.5.5 0 0 0 .35-.85L5.5 3.21z\'/></svg>")';
        cursor.style.backgroundSize = 'contain';
        cursor.style.pointerEvents = 'none';
        cursor.style.zIndex = '100000';
        document.body.appendChild(cursor);

        document.addEventListener('mousemove', (e) => {
            cursor.style.left = e.pageX + 'px';
            cursor.style.top = e.pageY + 'px';
        });
    });

    await recorder.start(outputPath);
    console.log("Recording started...");

    // Start up near the URL bar
    await page.mouse.move(500, 62);
    await new Promise(r => setTimeout(r, 1500));

    // Move to the Gittensor Extension Icon (approx x=740, y=62)
    await page.mouse.move(740, 62, { steps: 20 });
    await new Promise(r => setTimeout(r, 800));

    // Click the extension icon to trigger the injection
    await page.mouse.down();
    await new Promise(r => setTimeout(r, 100));
    await page.mouse.up();
    console.log("Clicked extension icon");

    // Wait for the popup to show, hide, and badges to appear
    await new Promise(r => setTimeout(r, 2600));

    // Move to "Yes" badge (sprouts left of the bet row, approx x=180, y=360)
    await page.mouse.move(180, 400, { steps: 30 });
    await new Promise(r => setTimeout(r, 1200));

    // Move to "No" badge
    await page.mouse.move(180, 480, { steps: 20 });
    await new Promise(r => setTimeout(r, 2000));

    await recorder.stop();
    await browser.close();
    console.log(`Finished recording ${outputPath}`);
}

recordPage("", "pr9_extension.mp4").catch(console.error);
