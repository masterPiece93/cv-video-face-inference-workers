# Cloud Resources Design

# Bucket Structure

- Bucket Name : **tdx-gta-{ENV}-external-candidature-records**  
- Bucket Paths

```
- .../.../.../{candidateEmail}/{candidateUUID}
    
    - video_snippets/
        profile/
        interview_1/
		1.mp4
		2.mp4
        interview_2/
        .
        .
        interview_n/
    - video_face_encodings/
        profile/
            profile.npy
        interview_1/
            interview_1.npy
        interview_2/
            interview_2.npy
        .
        .
        interview_n/
            interview_n.npy
    - sampled_frames/
	profile/
		1.png
       interview_1/
		1.png
		2.png
		3.png
		.
		.
		n.png
       interview_2/
       .
       .
       interview_n/
    - onboarding_reference/
        img1.jpg
        img2.jpg
        img3.jpg
    - stages/
        processing.json
            {
                "message_id": "",
		   "event_id"  : "",	
                "started"   : "<time>",
                "completed" : null,
            } OR
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : "<time>",
            } OR
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : null,
                "status": "OK|ERROR"
            }
        encoding.json
            {
                "message_id": "",
		   "event_id": "",
                "started"   : "<time>",
                "completed" : null,
            }
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : "<time>",
            }
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : null,
                "status": "OK|ERROR"
            }
        verification.json
            {
                "message_id": "",
                ,
                "started"   : "<time>",
                "completed" : null,
            }
            {
                "message_id": "",
                ,
                "started"   : "<time>",
                "completed" : "<time>",
            }
            {
                "message_id": "",
                ,
                "started"   : "<time>",
                "completed" : null,
                "status": "OK|ERROR"
            }
        aggregation.json
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : null,
            }
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : "<time>",
            }
            {
                "message_id": "",
                "started"   : "<time>",
                "completed" : null,
                "status": "OK|ERROR"
            }
```

# 

# ---

#  Worker Request

## Sheet Upload and Video Verification Flow

- **`GoLang Source Worker o----Request----> Python Encoding Worker`**  
    
  - Topic Name : tdx-gta-dev-external-candidate-video-encoding

```json
{
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {
    "first_name": "Jane",
    "last_name": "Doe",
    "phone": "+1-555-0100",
    "application_id": 4321
    },
    "org_id": "org-42",
    "org_alias": "acme-inc",
    "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
    "event_id": "xxxzzzqqqwww",
    "lookup_map": {
        "profile": "profile",
        "interviews": ["interview_1", "interview_2", "interview_3"]
    }
}
```

- **`Python Encoding Worker o----Request----> Python Verification Worker`**  
    
  - Topic Name : tdx-gta-dev-external-candidate-video-verification

```json
{
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+1-555-0100",
        "application_id": 4321
    },
    "org_id": "org-42",
    "org_alias": "acme-inc",
    "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
    "event_id": "xxxzzzqqqwww",
    "lookup_map": {
        "profile": "profile",
        "interviews": ["interview_1", "interview_2", "interview_3"]
    }
    "sampled_frames": {
        "profile": ["1.png","2.png","3.png","4.png",],
        "interviews": {
            "interview_1": ["1.png","2.png","3.png","4.png",],
            "interview_2": ["1.png","2.png","3.png","4.png",]
        }
    }
}
```

- **`Python Verification Worker o----Request----> GoLang Sink Worker`**  
    
  - Topic Name : tdx-gta-dev-external-candidate-verification-result

```json
{
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+1-555-0100",
        "application_id": 4321
    },
    "org_id": "org-42",
    "org_alias": "acme-inc"
    "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
    "event_id": "xxxzzzqqqwww",
    "status": {
        "similar_face_count": 0,1,2+,
        "profile_match": True|False|Null,
	 "matches": {
"interview_1": true|false,
"interview_2": true|false
}
    },
    "sampled_frames": {
        "profile": ["1.png","2.png","3.png","4.png",],
        "interviews": {
            "interview_1": ["1.png","2.png","3.png","4.png",],
            "interview_2": ["1.png","2.png","3.png","4.png",]
        }
    },
    "lookup_map": {
        "profile": "profile",
        "interviews": ["interview_1", "interview_2", "interview_3"]
    }
}
```

* status.`similar_face_count` tells about how many similar faces are found across all the interview videos .  
  * If 0 \=\> no common face in any interview video  
  * If 1 \=\> a single person’s face is found common across all the interview videos  
  * If \> 1 ( 2, 3, … n ) \=\> many person’s faces are found common across all the interview videos  
* status.`profile_match` tells if the person in the profile video is matched/present in any of the interview videos .  
  * If null \=\> profile video was not present OR no interview video present .  
  * If True \=\> profile video person is present in any one of the interview  
  * If False \=\> profile video person is Not present in any one of the interview  
* status.`matches` tells about the profile match with each of the interview video  
  * `matches.interview_1`  :   
    * True	: person in profile video is present in interview 1  
    * False	: person in profile video is Not present in interview 1  
  * `matches.interview_2`  :  
    * Follows Same as above  
  * .  
  * .  
  * .  
  * `matches.interview_n`  :  
    * Follows Same as First  
* NOTE : based on the values of `status section ( similar_face_count , profile_match  and matches the GoLang worker makes the inference accordingly for UI display )`

## OnBoarding Image Verification Flow

- **`GoLang Proxy Worker o----Request----> Python On-boarding Verification Worker`**  
    
  - Topic Name : tdx-gta-dev-external-candidate-ob-verification

```json
{
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+1-555-0100",
        "application_id": 4321
    },
    "org_id": "org-42",
    "org_alias": "acme-inc"
    "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
    "event_id": "xxxzzzqqqwww",
    "match_against": ["profile", "interview_1"],
    "lookup_map": {
        "profile": "profile",
        "interviews": ["interview_1", "interview_2"]
    },
    "onboarding_reference_path": "onboarding_reference/"
}
```

* `match_against` : the worker will match the `onboarding_reference_path` against the identifiers mentioned in the array of values of this field  
  * For e.g : if it’s value is `["profile", "interview_1"]` , the worker will encode the images at the `onboarding_reference_path` and and will perform similarity check against the `"profile"` encodings ( through `lookup_map` ) and against the `"interview_1"` encodings (  through `lookup_map` ) .  
  * NOTE : Ultimate consistency should be there with `lookup_map` , if the values mentioned in  `match_against` are not present in `lookup_map` , then the results will be skipped for that particular `match_against` value .

- **`Python On-boarding Verification Worker o----Request----> GoLang Worker`**  
    
  - Topic Name : tdx-gta-dev-external-candidate-ob-verification-result

```json
{ 
    "candidate_email": "jane.doe@example.com",
    "candidate_uid": "cand-98765",
    "extra_info": {
        "first_name": "Jane",
        "last_name": "Doe",
        "phone": "+1-555-0100",
        "application_id": 4321
    },
    "org_id": "org-42",
    "org_alias": "acme-inc"
    "bucket_name": "tdx-{ENV}-external-sheet-candidature-records",
    "event_id": "xxxzzzqqqwww",
    "status": {
        "matches": {
            "profile": True,
            "interview_1": False,
        }
    },
}
```

---

Candidate Metadata Column:

 `"snippet_locations": {`  
       `// names of the final snippets`  
     `"profile": "profile.mpeg",`  
     `"interviews": {`  
          `"interview_1": ["1.mpeg","2.mpeg","3.mpeg","4.mpeg"],`   
          `"interview_2": ["1.mpeg"]`  
     `}`  
 `}`  
`"sampled_frames": {`  
 `// names of the final sampled frames`  
        `"profile": ["1.png","2.png","3.png","4.png",],`  
        `"interviews": {`  
            `"interview_1": ["1.png","2.png","3.png","4.png",],`  
            `"interview_2": ["1.png","2.png","3.png","4.png",]`  
        `}`  
`}`  
`"lookup_map": {`  
        `"profile": "profile",`  
        `"interviews": ["interview_1", "interview_2"]`  
`}`

---

## Base Paths

`"snippet_base_path":  "video_snippets/"`  
`"encoding_base_path": "video_face_encodings/"`  
`"Frame_location":     "video_sampled_frames/"`

---

**`To be sent to UI (Tentative)`**

`{`  
  `“Profile: {`  
  			`“Snippets”: [“1.mpeg/mp4”],`  
   			`“Sampled_Frames”: [“1.jpg”, “2.png”]`

             `}`  
`“Interviews”: {`

			`“Interview_1”: {`  
   			`“Sampled_Frames”: [“1.jpg”, “2.png”]`  
  					`“Snippets”: [“1.mpeg/mp4”, “2.mpeg/mp4”],`  
             				`}`  
			`“Interview_2”: {`  
   			`“Sampled_Frames”: [“1.jpg”, “2.png”]`  
  					`“Snippets”: [“1.mpeg/mp4”],`  
             				`}`  
			  
`}`

`}`  