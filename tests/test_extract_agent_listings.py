import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from src.extract_agent_listings import TargetPage, build_parser, discover_targets, extract_listings_from_html, filter_records
from src.extract_agent_listings import filter_records_category
from src.extract_agent_listings import extract_detail_page_record
from src.extract_agent_listings import filter_records_since
from src.extract_agent_listings import render_output


class ExtractAgentListingsTests(unittest.TestCase):
    def test_discover_targets_from_har(self):
        with TemporaryDirectory() as temp_dir:
            har_path = Path(temp_dir) / "sample.har"
            har_path.write_text(
                json.dumps(
                    {
                        "log": {
                            "entries": [
                                {
                                    "request": {
                                        "url": "https://www.har.com/realestatepro/sold-by-agent/cpt",
                                        "headers": [{"name": "User-Agent", "value": "Firefox"}],
                                    },
                                    "response": {
                                        "content": {
                                            "mimeType": "text/html",
                                            "text": "<html><body>ok</body></html>",
                                        }
                                    },
                                },
                                {
                                    "request": {
                                        "url": (
                                            "https://www.har.com/search/dosearch"
                                            "?soldoffice=brda01&all_status=closd"
                                        ),
                                        "headers": [{"name": "Accept", "value": "text/html"}],
                                    },
                                    "response": {"content": {"mimeType": "text/html"}},
                                },
                                {
                                    "request": {"url": "https://www.har.com/search/dosearch?for_sale=1"},
                                    "response": {"content": {"mimeType": "text/html"}},
                                },
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            targets = discover_targets([har_path])

        self.assertEqual(
            [target.url for target in targets],
            [
                "https://www.har.com/realestatepro/sold-by-agent/cpt",
                "https://www.har.com/search/dosearch?soldoffice=brda01&all_status=closd",
            ],
        )
        self.assertEqual(targets[0].embedded_html, "<html><body>ok</body></html>")

    def test_extract_listings_from_anchor_blocks(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <div class="listing-card">
          <a href="/houston/123-main-street/homedetail/11111111">123 Main Street</a>
          <span>Sold: 03/17/2024</span>
          <span>Sold Price: $315,000</span>
          <span>Lot Size: 7,250 sqft</span>
          <span>Houston, TX 77021</span>
        </div>
        <div class="listing-card">
          <a href="/houston/456-elm-street/homedetail/22222222">456 Elm Street</a>
          <span>Closed on January 5, 2025</span>
          <span>$410,500</span>
          <span>0.20 acres lot</span>
          <span>Houston, TX 77004</span>
        </div>
        """

        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )

        self.assertEqual(
            [record.listing_url for record in records],
            [
                "https://www.har.com/houston/123-main-street/homedetail/11111111",
                "https://www.har.com/houston/456-elm-street/homedetail/22222222",
            ],
        )
        self.assertEqual(records[0].sold_date, "2024-03-17")
        self.assertEqual(records[0].price, 315000)
        self.assertEqual(records[0].lot_size, "7,250 sqft")
        self.assertEqual(records[0].zip_code, "77021")
        self.assertEqual(records[1].sold_year, 2025)
        self.assertEqual(records[1].lot_size, "0.20 acres")

    def test_filter_records_by_year_and_zip(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <a href="/houston/one/homedetail/1">One</a>
        <span>Sold: 02/01/2024</span>
        <span>$100,000</span>
        <span>77021</span>
        <a href="/houston/two/homedetail/2">Two</a>
        <span>Sold: 02/01/2025</span>
        <span>$200,000</span>
        <span>77022</span>
        """

        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )
        filtered = filter_records(records, year=2024, zip_code="77021")

        self.assertEqual(len(filtered), 1)
        self.assertTrue(filtered[0].listing_url.endswith("/1"))

    def test_filter_records_since(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <a href="/houston/one/homedetail/1">One</a>
        <span>Sold: 02/01/2024</span>
        <span>$100,000</span>
        <span>77021</span>
        <a href="/houston/two/homedetail/2">Two</a>
        <span>Sold: 03/01/2025</span>
        <span>$200,000</span>
        <span>77021</span>
        """
        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )

        filtered = filter_records_since(records, "2025-01-01")

        self.assertEqual(len(filtered), 1)
        self.assertTrue(filtered[0].listing_url.endswith("/2"))

    def test_filter_records_category(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <div>Recently Rented</div>
        <a href="/houston/one/homedetail/1">One</a>
        <span>Rented on 02/01/2024</span>
        <span>$1,900</span>
        <span>77021</span>
        <a href="/houston/two/homedetail/2">Two</a>
        <span>Sold: 03/01/2025</span>
        <span>$200,000</span>
        <span>77021</span>
        """
        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )

        rentals = filter_records_category(records, "rental")
        sales = filter_records_category(records, "sale")

        self.assertEqual(len(rentals), 1)
        self.assertEqual(rentals[0].category, "rental")
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0].category, "sale")

    def test_extract_listings_from_flat_js_objects(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <script>
        window.__LISTINGS__ = [
          {"sdate":1710633600,"property_url":"\\/homedetail\\/123-main-houston-tx-77021\\/1001","salesprice":315000,"lotsize":7250,"lotsizeunit":"squar","fullstreetaddress":"123 Main St Houston TX 77021"},
          {"sdate":1736035200,"web_url":"\\/homedetail\\/456-elm-houston-tx-77004\\/1002","listprice":"$410,500","acres":0.2,"fullstreetaddress":"456 Elm St Houston TX 77004"}
        ];
        </script>
        """

        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="live_fetch",
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].sold_date, "2024-03-17")
        self.assertEqual(records[0].lot_size, "7,250 sqft")
        self.assertEqual(records[0].zip_code, "77021")
        self.assertEqual(records[1].price, 410500)
        self.assertEqual(records[1].lot_size, "0.2 acres")

    def test_extract_listings_prefers_url_zip_and_acres(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <script>
        window.__LISTINGS__ = [
          {"sdate":1692766800,"property_url":"\\/homedetail\\/9503-gates-loop-manvel-tx-77578\\/776840","salesprice":510000,"lotsize":174240,"lotsizeunit":"acre","acres":4,"fullstreetaddress":"9503 Gates Loop"}
        ];
        </script>
        """

        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )

        self.assertEqual(records[0].zip_code, "77578")
        self.assertEqual(records[0].lot_size, "4 acres")

    def test_extract_listings_merges_json_and_anchor_data(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/search/dosearch?soldoffice=brda01&all_status=closd",
            headers={},
            embedded_html=None,
        )
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": "Product",
              "url": "/homedetail/3626-lehall-ave-a-b-houston-tx-77021/17526416?sid=10527340"
            }
          ]
        }
        </script>
        <a href="/homedetail/3626-lehall-ave-a-b-houston-tx-77021/17526416?sid=10527340">3626 Lehall Ave A-B</a>
        <div>Houston, TX 77021</div>
        <div>03/04/2026</div>
        <div>$482K - $552K</div>
        """

        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].zip_code, "77021")
        self.assertEqual(records[0].sold_date, "2026-03-04")

    def test_extracts_lot_size_from_har_card_text(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/search/dosearch?soldoffice=brda01&all_status=closd",
            headers={},
            embedded_html=None,
        )
        html = """
        <div>Represented: Seller</div>
        <a href="/homedetail/7129-sidney-st-houston-tx-77021/2996851?sid=10226038">7129 Sidney St A-B</a>
        <div>Houston, TX 77021</div>
        <div>10/30/2025</div>
        <div>$482K - $552K</div>
        <div>Listed for $510,000</div>
        <div>Multi-Family - Duplex</div>
        <div>In Foster Place in University Area (Marketarea)</div>
        <div><span>3,000</span> Sqft.</div>
        <div><span>5,150</span> lot Sqft.</div>
        <div><span>3</span> bedrooms</div>
        <div><span>2</span> full baths</div>
        <div><span>2</span> stories</div>
        <div><span>2025</span> year built</div>
        """

        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].lot_size, "5,150 sqft")
        self.assertEqual(records[0].listed_price, 510000)
        self.assertEqual(records[0].price_band_low, 482000)
        self.assertEqual(records[0].price_band_high, 552000)
        self.assertEqual(records[0].property_type, "Multi-Family - Duplex")
        self.assertEqual(records[0].represented_side, "Seller")
        self.assertEqual(records[0].neighborhood, "Foster Place")
        self.assertEqual(records[0].market_area, "University Area")
        self.assertEqual(records[0].building_sqft, 3000)
        self.assertEqual(records[0].beds, 3)
        self.assertEqual(records[0].full_baths, 2)
        self.assertEqual(records[0].stories, 2)
        self.assertEqual(records[0].year_built, 2025)

    def test_extract_detail_page_record_reads_lot_size(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": ["Place", "SingleFamilyResidence"],
              "url": "https://www.har.com/homedetail/6401-goforth-st-houston-tx-77021/16588467",
              "address": {"postalCode": "77021"},
              "offers": {"price": 339000},
              "additionalProperty": [
                {"name": "Lot Size", "value": "4,208 sq ft"},
                {"name": "Closed Date", "value": "02/17/2026"}
              ]
            }
          ]
        }
        </script>
        """

        record = extract_detail_page_record(
            html,
            listing_url="https://www.har.com/homedetail/6401-goforth-st-houston-tx-77021/16588467",
            target=target,
            extraction_mode="detail_fetch",
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.lot_size, "4,208 sqft")
        self.assertEqual(record.sold_date, "2026-02-17")
        self.assertEqual(record.zip_code, "77021")

    def test_extract_detail_page_record_classifies_rental(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": ["Place", "SingleFamilyResidence"],
              "name": "3541 Rebecca St",
              "url": "https://www.har.com/homedetail/3541-rebecca-st-houston-tx-77021/9274976",
              "address": {
                "streetAddress": "3541 Rebecca St",
                "addressLocality": "Houston",
                "addressRegion": "TX",
                "postalCode": "77021"
              },
              "offers": {"price": 1975},
              "additionalProperty": [
                {"name": "listingType", "value": "Recently Rented"},
                {"name": "Property Type", "value": "Rental"},
                {"name": "Lot Size", "value": "5,000 sq ft"},
                {"name": "Closed Date", "value": "09/10/2025"},
                {"name": "Architecture Style", "value": "Traditional"}
              ]
            }
          ]
        }
        </script>
        """

        record = extract_detail_page_record(
            html,
            listing_url="https://www.har.com/homedetail/3541-rebecca-st-houston-tx-77021/9274976",
            target=target,
            extraction_mode="detail_fetch",
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.category, "rental")
        self.assertEqual(record.property_type, "Rental")
        self.assertEqual(record.address, "3541 Rebecca St, Houston, TX 77021")

    def test_extract_detail_page_record_deduplicates_full_name_address(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/realestatepro/sold-by-agent/cpt",
            headers={},
            embedded_html=None,
        )
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": ["Place", "SingleFamilyResidence"],
              "name": "3541 Rebecca St, Houston, TX 77021",
              "url": "https://www.har.com/homedetail/3541-rebecca-st-houston-tx-77021/9274976",
              "address": {
                "streetAddress": "3541 Rebecca St",
                "addressLocality": "Houston",
                "addressRegion": "TX",
                "postalCode": "77021"
              },
              "offers": {"price": 1975},
              "additionalProperty": [
                {"name": "listingType", "value": "Recently Rented"},
                {"name": "Property Type", "value": "Rental"},
                {"name": "Closed Date", "value": "09/10/2025"}
              ]
            }
          ]
        }
        </script>
        """

        record = extract_detail_page_record(
            html,
            listing_url="https://www.har.com/homedetail/3541-rebecca-st-houston-tx-77021/9274976",
            target=target,
            extraction_mode="detail_fetch",
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.address, "3541 Rebecca St, Houston, TX 77021")

    def test_render_output_comp_profile(self):
        target = TargetPage(
            source_har="sample.har",
            url="https://www.har.com/search/dosearch?soldoffice=brda01&all_status=closd",
            headers={},
            embedded_html=None,
        )
        html = """
        <div>Represented: Seller</div>
        <a href="/homedetail/7129-sidney-st-houston-tx-77021/2996851?sid=10226038">7129 Sidney St A-B</a>
        <div>Houston, TX 77021</div>
        <div>10/30/2025</div>
        <div>$482K - $552K</div>
        <div>Listed for $510,000</div>
        <div>Multi-Family - Duplex</div>
        <div>In Foster Place in University Area (Marketarea)</div>
        <div><span>3,000</span> Sqft.</div>
        <div><span>5,150</span> lot Sqft.</div>
        <div><span>3</span> bedrooms</div>
        <div><span>2</span> full baths</div>
        <div><span>2</span> stories</div>
        <div><span>2025</span> year built</div>
        """
        records = extract_listings_from_html(
            html,
            page_url=target.url,
            target=target,
            extraction_mode="embedded",
        )
        rendered = render_output(records, "markdown", profile="comp")

        self.assertIn("Neighborhood", rendered)
        self.assertIn("$510,000", rendered)
        self.assertIn("$482,000-$552,000", rendered)
        self.assertIn("University Area", rendered)

    def test_parser_rejects_year_and_since_together(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["sample.har", "--year", "2025", "--since", "2025-01-01"])


if __name__ == "__main__":
    unittest.main()
