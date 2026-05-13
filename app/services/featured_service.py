import logging

logger = logging.getLogger(__name__)


def process_home_sections(scraper, page, url, config, url_patterns):
    result = scraper.extract_home_sections(page, url, config)
    if "error" in result:
        return result

    from app.api.home import _process_featured_items
    from app.services.metadata_service import populate_metadata_for_dicts
    from app.api.db_utils import save_animes_to_db, save_episodes_to_db

    sections_data = {}
    all_items_to_populate = []

    if "sections" in result:
        for section_name, items in result["sections"].items():
            processed = _process_featured_items(items, scraper, url_patterns)
            sections_data[section_name] = processed
            all_items_to_populate.extend(processed)
    else:
        items = result.get("episode_urls", [])
        processed = _process_featured_items(items, scraper, url_patterns)
        sections_data["featured"] = processed
        all_items_to_populate.extend(processed)

    populate_metadata_for_dicts(all_items_to_populate)

    animes_to_save = [item for item in all_items_to_populate if item["item_type"] in ["anime", "movie"]]
    episodes_to_save = [item for item in all_items_to_populate if item["item_type"] == "episode"]

    if animes_to_save:
        save_animes_to_db(animes_to_save)
    if episodes_to_save:
        save_episodes_to_db(episodes_to_save)

    ordered_sections = {}
    for key in ["releases", "latest_episodes", "latest_animes", "latest_movies"]:
        if key in sections_data:
            ordered_sections[key] = sections_data[key]

    for key, value in sections_data.items():
        if key not in ordered_sections:
            ordered_sections[key] = value

    return {
        "source": None,
        "url": url,
        "sections": ordered_sections,
        "total_items": len(all_items_to_populate),
        "cached": False
    }
